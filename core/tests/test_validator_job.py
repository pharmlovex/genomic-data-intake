"""Run the validator job's main() against generated data, with Object Storage and NoSQL faked."""

import importlib.util
import io
import pathlib
import subprocess
import sys

from intake_core import BatchStatus, FileStatus

from test_service_flow import BATCH, FakeStore

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_validator():
    spec = importlib.util.spec_from_file_location("run_validation", ROOT / "validator" / "run_validation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validator_job_quarantines_only_the_truncated_file(tmp_path, monkeypatch):
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_test_data.py"), "--out", str(tmp_path),
                    "--samples", "2", "--pairs", "300", "--with-problems"], check=True, capture_output=True)
    FakeStore.batches, FakeStore.files = {}, {}
    FakeStore().put_batch(BATCH, BatchStatus.VALIDATING, samplesheet_csv=(tmp_path / "samplesheet.csv").read_text())
    quarantined = []

    class LocalStorage:
        def open_stream(self, key):
            return io.BytesIO((tmp_path / "fastq" / key.split("/")[-1]).read_bytes())

        def quarantine(self, batch_id, filename):
            quarantined.append(filename)

    mod = load_validator()
    monkeypatch.setattr(mod, "Store", FakeStore)
    monkeypatch.setattr(mod, "Storage", LocalStorage)
    monkeypatch.setattr(mod, "publish_report", lambda store, b: {"status": "x"})
    monkeypatch.setattr(mod.jobs, "delete_finished_jobs", lambda b: 0)

    assert mod.main(["--batch-id", BATCH, "--sample-id", "S001"]) == 0
    assert FakeStore.files[BATCH]["S001_R1.fastq.gz"].status == FileStatus.VALID
    assert FakeStore.files[BATCH]["S001_R1.fastq.gz"].read_count == 300

    assert mod.main(["--batch-id", BATCH, "--sample-id", "S002"]) == 1
    assert quarantined == ["S002_R2.fastq.gz"]
    assert FakeStore.files[BATCH]["S002_R1.fastq.gz"].status == FileStatus.AWAITING_PAIR
    r2 = FakeStore.files[BATCH]["S002_R2.fastq.gz"]
    assert r2.status == FileStatus.QUARANTINED and "R2" in r2.reason
