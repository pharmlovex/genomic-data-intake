"""Validation job: runs in an OCI Container Instance, one per sample.

1. Stream R1 and R2 from Object Storage and validate them together.
2. Compare the MD5 of each compressed file with the sample sheet.
3. Record VALID, or QUARANTINED with the exact reason for the file at fault.
4. Recompute the batch report and notify the lab on milestones.

Safe to re-run: it only writes final states for this sample.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from intake_core import FileRecord, FileStatus, validate_pair, validation_outcome
from intake_core.cloud import jobs
from intake_core.cloud.service import publish_report
from intake_core.cloud.storage import Storage, raw_key
from intake_core.cloud.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("validator")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--sample-id", required=True)
    args = ap.parse_args(argv)

    store, storage = Store(), Storage()
    batch = store.get_batch(args.batch_id)
    sheet = store.get_samplesheet(batch)
    sample = sheet.samples[args.sample_id]
    r1, r2 = sample.r1, sample.r2

    started = time.monotonic()
    log.info("validating %s/%s: %s + %s", args.batch_id, args.sample_id, r1.filename, r2.filename)
    result = validate_pair(storage.open_stream(raw_key(args.batch_id, r1.filename)),
                           storage.open_stream(raw_key(args.batch_id, r2.filename)))
    elapsed = time.monotonic() - started
    gb = (result.r1.compressed_bytes + result.r2.compressed_bytes) / 1e9
    log.info("read %.2f GB in %.0f s (%.0f MB/s)", gb, elapsed, gb * 1000 / elapsed if elapsed else 0)

    by_name = {r1.filename: result.r1, r2.filename: result.r2}
    for filename, (status, reason) in validation_outcome(sample, result).items():
        stats = by_name[filename]
        if status == FileStatus.QUARANTINED:
            storage.quarantine(args.batch_id, filename)
        store.put_file(args.batch_id, FileRecord(filename, status, args.sample_id, reason, stats.md5 if result.ok else None,
                                                 stats.read_count))
        log.info("%s -> %s%s", filename, status.value, f" ({reason})" if reason else "")

    report = publish_report(store, args.batch_id)
    log.info("batch %s is now %s", args.batch_id, report["status"])

    try:
        jobs.delete_finished_jobs(args.batch_id)  # clean up earlier finished jobs of this batch
    except Exception:  # cleanup must never fail the validation
        log.exception("cleanup of finished jobs failed")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
