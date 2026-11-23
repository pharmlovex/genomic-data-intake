"""Pure decision logic: what to do when a file or a sample sheet arrives.

No cloud calls here, so the whole state machine is unit-tested. The functions
and the validator call these and then persist the result.

Key design choice: files are never rejected for arriving before the sample
sheet. They are staged as AWAITING_SAMPLESHEET and reconciled when it lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .samplesheet import SampleSheet
from .status import BatchStatus, FileStatus


@dataclass
class FileRecord:
    filename: str
    status: FileStatus
    sample_id: str | None = None
    reason: str | None = None
    md5: str | None = None
    read_count: int | None = None


def status_on_arrival(filename: str, sheet: SampleSheet | None) -> FileStatus:
    """Status for a file that has just landed in Object Storage."""
    if sheet is None:
        return FileStatus.AWAITING_SAMPLESHEET
    if filename not in sheet.expected_files:
        return FileStatus.UNEXPECTED
    return FileStatus.AWAITING_PAIR


def reclassify_on_samplesheet(
    files: dict[str, FileRecord], sheet: SampleSheet
) -> dict[str, FileRecord]:
    """When the sample sheet arrives, re-label files that were staged before it."""
    out: dict[str, FileRecord] = {}
    for name, rec in files.items():
        if rec.status == FileStatus.AWAITING_SAMPLESHEET:
            new_status = status_on_arrival(name, sheet)
            sample = sheet.expected_files.get(name)
            out[name] = FileRecord(name, new_status, sample.sample_id if sample else None)
        else:
            out[name] = rec
    return out


def samples_ready_to_validate(files: dict[str, FileRecord], sheet: SampleSheet) -> list[str]:
    """Samples whose R1 and R2 have both arrived and have not been validated yet.

    Validation runs per sample, not per file, because the pair check
    (same read count, matching read names) needs both mates.
    """
    ready = []
    for sid, sample in sheet.samples.items():
        recs = [files.get(f.filename) for f in sample.files]
        if all(r is not None and r.status == FileStatus.AWAITING_PAIR for r in recs):
            ready.append(sid)
    return sorted(ready)


def validation_outcome(sample, result) -> dict[str, tuple[FileStatus, str | None]]:
    """Map a PairResult to a (status, reason) per file of the sample.

    Only the file at fault is quarantined, so the lab re-sends one file, not two:
      * a problem inside one file (R1/R2 structure, gzip, MD5) -> that file QUARANTINED,
        its mate AWAITING_PAIR (fine, but must be re-checked against the re-sent file)
      * a pairing problem (read counts or names differ) -> both QUARANTINED
    """
    r1, r2 = sample.r1, sample.r2
    if not result.ok:
        bad = {"R1": r1, "R2": r2}.get(result.failed_file)
        if bad is None:
            return {f.filename: (FileStatus.QUARANTINED, result.error) for f in (r1, r2)}
        mate = r2 if bad is r1 else r1
        return {bad.filename: (FileStatus.QUARANTINED, result.error),
                mate.filename: (FileStatus.AWAITING_PAIR, None)}

    out = {}
    for exp, stats in ((r1, result.r1), (r2, result.r2)):
        if stats.md5 != exp.md5:
            out[exp.filename] = (FileStatus.QUARANTINED,
                                 f"MD5 mismatch: expected {exp.md5}, got {stats.md5} (corrupted in transfer?)")
        else:
            out[exp.filename] = (FileStatus.VALID, None)
    if any(s == FileStatus.QUARANTINED for s, _ in out.values()):
        out = {k: (s, r) if s == FileStatus.QUARANTINED else (FileStatus.AWAITING_PAIR, None) for k, (s, r) in out.items()}
    return out


def mate_to_recheck(filename: str, sheet: SampleSheet, files: dict[str, FileRecord]) -> str | None:
    """A re-sent file's mate must be validated again with it: return the mate if it needs resetting."""
    exp = sheet.expected_files.get(filename)
    if exp is None:
        return None
    sample = sheet.samples[exp.sample_id]
    mate = sample.r2 if exp.read == 1 else sample.r1
    rec = files.get(mate.filename)
    if rec is not None and rec.status == FileStatus.VALID:
        return mate.filename
    return None


@dataclass
class BatchReport:
    batch_id: str
    status: BatchStatus
    counts: dict[str, int]
    attention: list[dict] = field(default_factory=list)  # what the lab must act on

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "status": self.status.value,
            "counts": self.counts,
            "needs_attention": self.attention,
        }


def build_report(batch_id: str, files: dict[str, FileRecord], sheet: SampleSheet | None) -> BatchReport:
    """Roll file states up into a batch status and a lab-facing report."""
    records = dict(files)
    if sheet is not None:
        for name, exp in sheet.expected_files.items():
            if name not in records:
                records[name] = FileRecord(name, FileStatus.MISSING, exp.sample_id)

    counts: dict[str, int] = {}
    for rec in records.values():
        counts[rec.status.value] = counts.get(rec.status.value, 0) + 1

    attention = [
        {"file": r.filename, "sample_id": r.sample_id, "status": r.status.value, "reason": r.reason}
        for r in sorted(records.values(), key=lambda r: r.filename)
        if r.status in (FileStatus.QUARANTINED, FileStatus.UNEXPECTED)
    ]

    statuses = {r.status for r in records.values()}
    if sheet is None:
        status = BatchStatus.OPEN
    elif attention:
        status = BatchStatus.NEEDS_ATTENTION
    elif statuses == {FileStatus.VALID}:
        status = BatchStatus.READY_FOR_ANALYSIS
    elif FileStatus.VALIDATING in statuses or FileStatus.VALID in statuses:
        status = BatchStatus.VALIDATING
    else:
        status = BatchStatus.SAMPLESHEET_RECEIVED

    return BatchReport(batch_id, status, counts, attention)
