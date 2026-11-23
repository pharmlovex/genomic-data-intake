"""End-to-end flow of the orchestration with in-memory fakes for the OCI services.

Scenario from the design interview: the lab uploads FASTQs before the sample
sheet, plus one stray file; the sheet arrives later; jobs launch per sample.
"""

import pytest

from intake_core import BatchStatus, FileRecord, FileStatus, parse_samplesheet
from intake_core.cloud import service

BATCH = "11111111-2222-3333-4444-555555555555"
MD5 = "0" * 32
SHEET = "sample_id,fastq_1,fastq_2,md5_1,md5_2\n" + "".join(
    f"{s},{s}_R1.fastq.gz,{s}_R2.fastq.gz,{MD5},{MD5}\n" for s in ("S001", "S002")
)


class FakeStore:
    batches: dict = {}
    files: dict = {}

    def put_batch(self, batch_id, status, lab_email="", samplesheet_csv="", created_at=None):
        self.batches[batch_id] = {"batch_id": batch_id, "status": status.value, "lab_email": lab_email,
                                  "samplesheet_csv": samplesheet_csv, "created_at": created_at}

    def get_batch(self, batch_id):
        return self.batches.get(batch_id)

    def get_samplesheet(self, batch):
        return parse_samplesheet(batch["samplesheet_csv"]) if batch.get("samplesheet_csv") else None

    def set_batch_status(self, batch, status):
        self.batches[batch["batch_id"]]["status"] = status.value

    def put_file(self, batch_id, rec):
        self.files.setdefault(batch_id, {})[rec.filename] = FileRecord(**vars(rec))

    def list_files(self, batch_id):
        return {k: FileRecord(**vars(v)) for k, v in self.files.get(batch_id, {}).items()}


class FakeStorage:
    reports = {}

    def write_report(self, batch_id, report):
        self.reports[batch_id] = report

    def write_samplesheet(self, batch_id, csv_text):
        self.reports[f"{batch_id}.csv"] = csv_text


@pytest.fixture
def env(monkeypatch):
    FakeStore.batches, FakeStore.files, FakeStorage.reports = {}, {}, {}
    launched, notes = [], []
    monkeypatch.setattr(service, "Store", FakeStore)
    monkeypatch.setattr(service, "Storage", FakeStorage)
    monkeypatch.setattr(service.jobs, "launch_validation", lambda b, s: launched.append(s) or f"ocid-{s}")
    monkeypatch.setattr(service, "notify", lambda subj, body: notes.append(subj))
    FakeStore().put_batch(BATCH, BatchStatus.OPEN, "lab@example.org")
    return launched, notes


def status_of(name):
    return FakeStore.files[BATCH][name].status


def test_files_before_samplesheet_then_reconcile_and_launch(env):
    launched, notes = env
    for f in ("S001_R1.fastq.gz", "S001_R2.fastq.gz", "S002_R1.fastq.gz", "stray.fastq.gz"):
        service.on_file_arrived(BATCH, f)
    assert status_of("S001_R1.fastq.gz") == FileStatus.AWAITING_SAMPLESHEET
    assert launched == []

    result = service.on_samplesheet(BATCH, SHEET)
    assert result["launched"] == ["S001"]          # S002 still missing its R2
    assert status_of("S001_R1.fastq.gz") == FileStatus.VALIDATING
    assert status_of("S002_R1.fastq.gz") == FileStatus.AWAITING_PAIR
    assert status_of("stray.fastq.gz") == FileStatus.UNEXPECTED
    assert result["report"]["status"] == BatchStatus.NEEDS_ATTENTION.value  # the stray file
    assert any("NEEDS_ATTENTION" in n for n in notes)

    service.on_file_arrived(BATCH, "S002_R2.fastq.gz")
    assert launched == ["S001", "S002"]


def test_batch_ready_when_all_valid(env):
    service.on_samplesheet(BATCH, SHEET)
    store = FakeStore()
    sheet = parse_samplesheet(SHEET)
    for name, exp in sheet.expected_files.items():
        store.put_file(BATCH, FileRecord(name, FileStatus.VALID, exp.sample_id))
    report = service.publish_report(store, BATCH)
    assert report["status"] == BatchStatus.READY_FOR_ANALYSIS.value
    assert FakeStore.batches[BATCH]["status"] == BatchStatus.READY_FOR_ANALYSIS.value


def test_file_for_unknown_batch_is_ignored(env):
    assert service.on_file_arrived("99999999-2222-3333-4444-555555555555", "x.fastq.gz") == {"ignored": True}


def test_resent_file_revalidates_with_its_mate(env):
    launched, _ = env
    service.on_samplesheet(BATCH, SHEET)
    store = FakeStore()
    # S001 was validated: R1 fine, R2 truncated (only R2 quarantined, R1 waits for it)
    store.put_file(BATCH, FileRecord("S001_R1.fastq.gz", FileStatus.AWAITING_PAIR, "S001"))
    store.put_file(BATCH, FileRecord("S001_R2.fastq.gz", FileStatus.QUARANTINED, "S001", "truncated"))
    launched.clear()
    service.on_file_arrived(BATCH, "S001_R2.fastq.gz")  # the lab re-sends R2
    assert launched == ["S001"]


def test_resending_one_file_of_a_valid_pair_rechecks_both(env):
    launched, _ = env
    service.on_samplesheet(BATCH, SHEET)
    store = FakeStore()
    store.put_file(BATCH, FileRecord("S001_R1.fastq.gz", FileStatus.VALID, "S001"))
    store.put_file(BATCH, FileRecord("S001_R2.fastq.gz", FileStatus.VALID, "S001"))
    launched.clear()
    service.on_file_arrived(BATCH, "S001_R1.fastq.gz")
    assert launched == ["S001"]
