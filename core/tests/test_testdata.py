"""The generated test batch validates as expected: good samples pass, the truncated one fails."""

import csv
import pathlib
import subprocess
import sys

from intake_core import parse_samplesheet, validate_pair

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "make_test_data.py"


def test_generated_batch(tmp_path):
    subprocess.run([sys.executable, str(SCRIPT), "--out", str(tmp_path), "--samples", "3", "--pairs", "500",
                    "--with-problems"], check=True, capture_output=True)
    sheet = parse_samplesheet((tmp_path / "samplesheet.csv").read_text())
    results = {}
    for sid, sample in sheet.samples.items():
        with open(tmp_path / "fastq" / sample.r1.filename, "rb") as a, open(tmp_path / "fastq" / sample.r2.filename, "rb") as b:
            res = validate_pair(a, b)
        results[sid] = res
        if res.ok:
            assert res.r1.md5 == sample.r1.md5 and res.r2.md5 == sample.r2.md5
            assert res.r1.read_count == 500
    assert results["S001"].ok and results["S003"].ok
    assert not results["S002"].ok
    assert (tmp_path / "fastq" / "stray_sample_R1.fastq.gz").exists()
    assert "stray_sample_R1.fastq.gz" not in sheet.expected_files
