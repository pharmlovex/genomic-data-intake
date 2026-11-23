"""Streaming validation of a paired-end FASTQ sample (R1 + R2, gzipped).

Reads both files in lockstep, so memory stays flat whatever the file size, and
computes the MD5 of the *compressed* bytes (what the sequencer's checksum
covers) in the same pass. A 100 GB file is read once, never held in memory.

Speed: records are checked in blocks of thousands with C-level bytes
operations rather than one at a time in Python. Measured on one core:
~250 MB/s of decompressed FASTQ (per-record Python was ~45 MB/s). See
README "Performance" for what that means for a 100 GB sample.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import zlib
from dataclasses import dataclass, field
from typing import BinaryIO

MIN_QUAL, MAX_QUAL = 33, 126  # Phred+33 printable range
CHUNK = 1024 * 1024
GC_SAMPLE_EVERY = 8  # estimate GC% from 1 in 8 decompressed blocks


class HashingReader(io.RawIOBase):
    """Wraps a binary stream and hashes every byte read through it."""

    def __init__(self, raw: BinaryIO):
        self._raw = raw
        self.md5 = hashlib.md5()
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        data = self._raw.read(len(b))
        if not data:
            return 0
        n = len(data)
        b[:n] = data
        self.md5.update(data)
        self.bytes_read += n
        return n

    def drain(self) -> None:
        """Hash any trailing bytes the decompressor did not need."""
        while chunk := self._raw.read(CHUNK):
            self.md5.update(chunk)
            self.bytes_read += len(chunk)


@dataclass
class ReadStats:
    md5: str = ""
    compressed_bytes: int = 0
    read_count: int = 0
    bases: int = 0  # bases in the sampled blocks used for GC%
    gc: int = 0

    @property
    def gc_percent(self) -> float:
        return round(100 * self.gc / self.bases, 2) if self.bases else 0.0


@dataclass
class PairResult:
    ok: bool
    error: str | None = None
    r1: ReadStats = field(default_factory=ReadStats)
    r2: ReadStats = field(default_factory=ReadStats)

    @property
    def failed_file(self) -> str | None:
        """'R1' or 'R2' when the problem is in one file; None when it's the pairing (or no error)."""
        for label in ("R1", "R2"):
            if self.error and (self.error.startswith(label + " ") or self.error.startswith(label + ":")):
                return label
        return None


_VALID_SEQ = b"ACGTNacgtn"
_VALID_QUAL = bytes(range(MIN_QUAL, MAX_QUAL + 1))


def _check_record(n: int, label: str, header: bytes, seq: bytes, plus: bytes, qual: bytes) -> None:
    """Slow, exact check of one record; used to pinpoint the record a block check flagged."""
    if not header.startswith(b"@"):
        raise ValueError(f"{label} record {n}: header must start with '@'")
    if not plus.startswith(b"+"):
        raise ValueError(f"{label} record {n}: third line must start with '+'")
    if len(seq) != len(qual):
        raise ValueError(f"{label} record {n}: sequence length {len(seq)} != quality length {len(qual)}")
    if not seq:
        raise ValueError(f"{label} record {n}: empty sequence")
    bad = seq.translate(None, _VALID_SEQ)
    if bad:
        raise ValueError(f"{label} record {n}: invalid base(s) {bytes(sorted(set(bad))).decode(errors='replace')!r}")
    if qual.translate(None, _VALID_QUAL):
        raise ValueError(f"{label} record {n}: quality outside Phred+33 range")


class _Records:
    """Decompresses in large chunks and hands out whole records as parallel lists.

    Checks run on a whole block at once with C-level bytes operations, which is
    what makes pure Python fast enough here (per-record Python code is ~10x slower).
    """

    def __init__(self, gz, label: str):
        self._gz, self.label = gz, label
        self._rest = b""
        self.eof = False
        self.h, self.s, self.p, self.q = [], [], [], []

    def fill(self, need: int) -> None:
        while len(self.h) < need and not self.eof:
            try:
                chunk = self._gz.read(CHUNK * 4)
            except (OSError, EOFError, zlib.error) as exc:
                raise ValueError(f"{self.label}: gzip stream is corrupt or truncated ({exc})") from exc
            if not chunk:
                self.eof = True
                tail = [ln for ln in self._rest.split(b"\n") if ln]
                if tail:
                    self.h += [None]  # marker: partial record at EOF
                return
            lines = (self._rest + chunk).split(b"\n")
            if b"\r" in chunk:
                lines = [ln.rstrip(b"\r") for ln in lines]
            whole = (len(lines) - 1) // 4 * 4  # last element is always an incomplete line
            self._rest = b"\n".join(lines[whole:])
            block = lines[:whole]
            self.h += block[0::4]
            self.s += block[1::4]
            self.p += block[2::4]
            self.q += block[3::4]

    def take(self, k: int):
        out = (self.h[:k], self.s[:k], self.p[:k], self.q[:k])
        del self.h[:k], self.s[:k], self.p[:k], self.q[:k]
        return out


def _starts_all(lines, prefix: bytes) -> bool:
    """True if every line starts with prefix (one C-level pass over the joined block)."""
    return lines[0].startswith(prefix) and b"\n".join(lines).count(b"\n" + prefix) == len(lines) - 1


def _check_block(first_n: int, label: str, h, s, p, q) -> None:
    ok = (
        _starts_all(h, b"@")
        and _starts_all(p, b"+")
        and list(map(len, s)) == list(map(len, q))
        and min(map(len, s)) > 0
        and not b"".join(s).translate(None, _VALID_SEQ)
        and not b"".join(q).translate(None, _VALID_QUAL)
    )
    if not ok:  # find and report the exact record
        for i, rec in enumerate(zip(h, s, p, q)):
            _check_record(first_n + i, label, *rec)


def _names(headers) -> list[bytes]:
    """Read name = header up to the first whitespace, minus a trailing /1 or /2."""
    names = [hd.split(None, 1)[0] for hd in headers]
    joined = b"\n".join(names) + b"\n"
    if b"/1\n" in joined or b"/2\n" in joined:  # older Illumina style: @name/1
        names = [nm[:-2] if nm[-2:] in (b"/1", b"/2") else nm for nm in names]
    return names


def _names_match(h1, h2) -> int:
    """-1 if every R1/R2 read name pairs up, else the index of the first mismatch."""
    # Fast path for Illumina 1.8+ headers, which differ only in the read number: "@name 1:N:..." vs "@name 2:N:..."
    if b"\n".join(h1) == b"\n".join(h2).replace(b" 2:", b" 1:"):
        return -1
    n1, n2 = _names(h1), _names(h2)
    if n1 == n2:
        return -1
    return next(i for i, (x, y) in enumerate(zip(n1, n2)) if x != y)


def validate_pair(r1_raw: BinaryIO, r2_raw: BinaryIO) -> PairResult:
    """Validate R1/R2 together. Stops at the first problem with an exact, lab-readable reason."""
    h1, h2 = HashingReader(r1_raw), HashingReader(r2_raw)
    result = PairResult(ok=False)
    try:
        with gzip.open(io.BufferedReader(h1, CHUNK)) as f1, gzip.open(io.BufferedReader(h2, CHUNK)) as f2:
            a, b = _Records(f1, "R1"), _Records(f2, "R2")
            done = 0  # records validated so far
            block_no = 0
            while True:
                a.fill(1)
                b.fill(1)
                k = min(len(a.h), len(b.h))
                if k == 0:
                    if a.h or b.h:
                        longer = a if a.h else b
                        if longer.h[0] is None:
                            raise ValueError(f"{longer.label} record {done + 1}: truncated (file ends mid-record)")
                        shorter = "R2" if longer is a else "R1"
                        raise ValueError(f"read count mismatch: {shorter} ends at record {done}, its mate continues")
                    break
                blocks = []
                for recs in (a, b):
                    if None in recs.h[:k]:
                        k = recs.h.index(None)
                        if k == 0:
                            raise ValueError(f"{recs.label} record {done + 1}: truncated (file ends mid-record)")
                block_no += 1
                for recs, stats in ((a, result.r1), (b, result.r2)):
                    h, s, p, q = recs.take(k)
                    _check_block(done + 1, recs.label, h, s, p, q)
                    stats.read_count += k
                    if block_no % GC_SAMPLE_EVERY == 1:  # GC% is a QC estimate: sample blocks, it's costly
                        joined = b"".join(s)
                        stats.bases += len(joined)
                        stats.gc += joined.count(b"G") + joined.count(b"C") + joined.count(b"g") + joined.count(b"c")
                    blocks.append(h)
                i = _names_match(blocks[0], blocks[1])
                if i >= 0:
                    raise ValueError(f"record {done + i + 1}: read names differ between R1 and R2 (files out of order?)")
                done += k
            if result.r1.read_count == 0:
                raise ValueError("files contain no reads")
        result.ok = True
    except (OSError, EOFError, zlib.error) as exc:  # gzip corruption or truncation
        result.error = f"gzip stream is corrupt or truncated ({exc})"
    except ValueError as exc:
        result.error = str(exc)

    for reader, stats in ((h1, result.r1), (h2, result.r2)):
        if result.ok:
            reader.drain()
        stats.md5 = reader.md5.hexdigest()
        stats.compressed_bytes = reader.bytes_read
    return result
