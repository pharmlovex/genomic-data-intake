"""Parse and check the sample sheet exported from the lab's LIMS.

Format (CSV, header required), modelled on the nf-core samplesheet with checksums:

    sample_id,fastq_1,fastq_2,md5_1,md5_2
    S001,S001_R1.fastq.gz,S001_R2.fastq.gz,<32 hex>,<32 hex>
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

REQUIRED_COLUMNS = ("sample_id", "fastq_1", "fastq_2", "md5_1", "md5_2")
_MD5 = re.compile(r"^[0-9a-f]{32}$")
_SAMPLE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_FILENAME = re.compile(r"^[A-Za-z0-9._-]+\.f(ast)?q\.gz$")


class SampleSheetError(ValueError):
    """The sample sheet is unusable. `problems` lists every issue found, not just the first."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ExpectedFile:
    filename: str
    sample_id: str
    read: int  # 1 or 2
    md5: str


@dataclass(frozen=True)
class Sample:
    sample_id: str
    r1: ExpectedFile
    r2: ExpectedFile

    @property
    def files(self) -> tuple[ExpectedFile, ExpectedFile]:
        return (self.r1, self.r2)


@dataclass(frozen=True)
class SampleSheet:
    samples: dict[str, Sample]

    @property
    def expected_files(self) -> dict[str, ExpectedFile]:
        return {f.filename: f for s in self.samples.values() for f in s.files}


def parse_samplesheet(text: str) -> SampleSheet:
    """Parse CSV text into a SampleSheet, collecting all problems before failing."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise SampleSheetError([f"missing column(s): {', '.join(missing)}"])

    problems: list[str] = []
    samples: dict[str, Sample] = {}
    seen_files: set[str] = set()

    for line_no, raw in enumerate(reader, start=2):
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue  # blank line
        sid = row["sample_id"]
        where = f"line {line_no}"
        if not _SAMPLE_ID.match(sid):
            problems.append(f"{where}: invalid sample_id {sid!r}")
            continue
        if sid in samples:
            problems.append(f"{where}: duplicate sample_id {sid!r}")
            continue

        files = []
        for read in (1, 2):
            name, md5 = row[f"fastq_{read}"], row[f"md5_{read}"].lower()
            if not _FILENAME.match(name):
                problems.append(f"{where}: fastq_{read} {name!r} must be a .fastq.gz/.fq.gz filename")
            elif name in seen_files:
                problems.append(f"{where}: file {name!r} listed more than once")
            if not _MD5.match(md5):
                problems.append(f"{where}: md5_{read} is not a 32-character hex checksum")
            seen_files.add(name)
            files.append(ExpectedFile(name, sid, read, md5))

        if files[0].filename == files[1].filename:
            problems.append(f"{where}: fastq_1 and fastq_2 are the same file")
        samples[sid] = Sample(sid, files[0], files[1])

    if not samples and not problems:
        problems.append("sample sheet has no samples")
    if problems:
        raise SampleSheetError(problems)
    return SampleSheet(samples)
