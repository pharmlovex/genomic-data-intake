"""Orchestration shared by the functions and the validator.

The pure decisions live in intake_core.reconcile; this module applies them to
the real store, storage, job launcher and notifications.
"""

from __future__ import annotations

import logging

from ..reconcile import (
    FileRecord, build_report, mate_to_recheck, reclassify_on_samplesheet, samples_ready_to_validate, status_on_arrival,
)
from ..status import BatchStatus, FileStatus
from . import client, jobs, settings
from .storage import Storage
from .store import Store

log = logging.getLogger("genome-intake")


def notify(subject: str, body: str) -> None:
    """Publish to the OCI Notifications topic the lab is subscribed to (e-mail)."""
    s = settings()
    if not s.topic_id:
        return
    import oci

    ons = client(oci.ons.NotificationDataPlaneClient)
    ons.publish_message(s.topic_id, oci.ons.models.MessageDetails(title=subject[:255], body=body))


def launch_ready_samples(store: Store, batch_id: str) -> list[str]:
    """Start a validation job for every sample whose R1 and R2 have both arrived."""
    batch = store.get_batch(batch_id)
    sheet = store.get_samplesheet(batch)
    if sheet is None:
        return []
    files = store.list_files(batch_id)
    launched = []
    for sample_id in samples_ready_to_validate(files, sheet):
        # Mark first, then launch: a duplicate launch is harmless (validation is idempotent),
        # a lost one would leave the sample stuck.
        for f in sheet.samples[sample_id].files:
            store.put_file(batch_id, FileRecord(f.filename, FileStatus.VALIDATING, sample_id))
        jobs.launch_validation(batch_id, sample_id)
        launched.append(sample_id)
    if launched:
        store.set_batch_status(batch, BatchStatus.VALIDATING)
    return launched


def on_file_arrived(batch_id: str, filename: str) -> dict:
    """Called by the Object Storage 'object created' event."""
    store = Store()
    batch = store.get_batch(batch_id)
    if batch is None:
        log.warning("upload to unknown batch %s (%s); ignoring", batch_id, filename)
        return {"ignored": True}
    sheet = store.get_samplesheet(batch)
    status = status_on_arrival(filename, sheet)
    sample = sheet.expected_files.get(filename) if sheet else None
    store.put_file(batch_id, FileRecord(filename, status, sample.sample_id if sample else None,
                                        reason="not listed in the sample sheet" if status == FileStatus.UNEXPECTED else None))
    if sheet is not None:  # a re-sent file is validated again together with its mate
        mate = mate_to_recheck(filename, sheet, store.list_files(batch_id))
        if mate:
            store.put_file(batch_id, FileRecord(mate, FileStatus.AWAITING_PAIR, sample.sample_id))
    launched = launch_ready_samples(store, batch_id)
    publish_report(store, batch_id)
    return {"file": filename, "status": status.value, "launched": launched}


def on_samplesheet(batch_id: str, csv_text: str) -> dict:
    """Store the sample sheet, reconcile files that arrived before it, launch what's ready."""
    from ..samplesheet import parse_samplesheet

    sheet = parse_samplesheet(csv_text)  # raises SampleSheetError with every problem listed
    store = Store()
    batch = store.get_batch(batch_id)
    store.put_batch(batch_id, BatchStatus.SAMPLESHEET_RECEIVED, batch.get("lab_email", ""), csv_text,
                    batch.get("created_at"))
    Storage().write_samplesheet(batch_id, csv_text)
    files = store.list_files(batch_id)
    for name, rec in reclassify_on_samplesheet(files, sheet).items():
        if rec.status != files[name].status:
            if rec.status == FileStatus.UNEXPECTED:
                rec.reason = "not listed in the sample sheet"
            store.put_file(batch_id, rec)
    launched = launch_ready_samples(store, batch_id)
    report = publish_report(store, batch_id)
    return {"samples": len(sheet.samples), "launched": launched, "report": report}


def publish_report(store: Store, batch_id: str, notify_lab: bool = False) -> dict:
    """Recompute the batch report, store it, and e-mail the lab on milestones."""
    batch = store.get_batch(batch_id)
    sheet = store.get_samplesheet(batch)
    report = build_report(batch_id, store.list_files(batch_id), sheet)
    previous = batch.get("status")
    store.set_batch_status(batch, report.status)
    Storage().write_report(batch_id, report.to_dict())

    milestone = report.status in (BatchStatus.READY_FOR_ANALYSIS, BatchStatus.NEEDS_ATTENTION)
    if notify_lab or (milestone and previous != report.status.value):
        lines = [f"Batch {batch_id}: {report.status.value}", f"Counts: {report.counts}"]
        lines += [f"- {a['file']} ({a['status']}): {a['reason']}" for a in report.attention]
        notify(f"Genome intake: batch {batch_id[:8]} {report.status.value}", "\n".join(lines))
    return report.to_dict()
