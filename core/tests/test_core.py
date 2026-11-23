import gzip
import hashlib
import io

import pytest

from intake_core import (
    BatchStatus, FileRecord, FileStatus, SampleSheetError, build_report, parse_samplesheet,
    reclassify_on_samplesheet, samples_ready_to_validate, status_on_arrival, validate_pair,
)

MD5 = "0" * 32


def sheet_text(*rows):
    lines = ["sample_id,fastq_1,fastq_2,md5_1,md5_2"]
    lines += [f"{s},{s}_R1.fastq.gz,{s}_R2.fastq.gz,{MD5},{MD5}" for s in rows]
    return "\n".join(lines) + "\n"


def fq(records):
    """records: list of (name, seq, qual) -> gzipped FASTQ bytes."""
    text = "".join(f"@{n}\n{s}\n+\n{q}\n" for n, s, q in records)
    return gzip.compress(text.encode())


def pair(n=3, length=10):
    r1 = [(f"read{i} 1:N:0:ACGT", "ACGTGCAN"[: min(8, length)].ljust(length, "A"), "I" * length) for i in range(n)]
    r2 = [(f"read{i} 2:N:0:ACGT", "TTGCAACG"[: min(8, length)].ljust(length, "T"), "I" * length) for i in range(n)]
    return r1, r2


# ---------- sample sheet ----------

def test_parse_valid_samplesheet():
    sheet = parse_samplesheet(sheet_text("S001", "S002"))
    assert set(sheet.samples) == {"S001", "S002"}
    assert sheet.expected_files["S001_R2.fastq.gz"].read == 2


def test_samplesheet_reports_all_problems_at_once():
    text = (
        "sample_id,fastq_1,fastq_2,md5_1,md5_2\n"
        f"S001,S001_R1.fastq.gz,S001_R2.fastq.gz,{MD5},nothex\n"
        f"S001,a.fastq.gz,b.fastq.gz,{MD5},{MD5}\n"
        f"S003,S003_R1.txt,S003_R2.fastq.gz,{MD5},{MD5}\n"
    )
    with pytest.raises(SampleSheetError) as err:
        parse_samplesheet(text)
    assert len(err.value.problems) == 3


def test_samplesheet_missing_column():
    with pytest.raises(SampleSheetError, match="missing column"):
        parse_samplesheet("sample_id,fastq_1\nS1,a.fastq.gz\n")


# ---------- reconciliation (files before sample sheet) ----------

def test_file_before_samplesheet_is_staged_not_rejected():
    assert status_on_arrival("S001_R1.fastq.gz", None) == FileStatus.AWAITING_SAMPLESHEET


def test_samplesheet_arrival_reclassifies_staged_files_and_finds_orphans():
    files = {
        "S001_R1.fastq.gz": FileRecord("S001_R1.fastq.gz", FileStatus.AWAITING_SAMPLESHEET),
        "S001_R2.fastq.gz": FileRecord("S001_R2.fastq.gz", FileStatus.AWAITING_SAMPLESHEET),
        "stray.fastq.gz": FileRecord("stray.fastq.gz", FileStatus.AWAITING_SAMPLESHEET),
    }
    sheet = parse_samplesheet(sheet_text("S001", "S002"))
    out = reclassify_on_samplesheet(files, sheet)
    assert out["S001_R1.fastq.gz"].status == FileStatus.AWAITING_PAIR
    assert out["stray.fastq.gz"].status == FileStatus.UNEXPECTED
    assert samples_ready_to_validate(out, sheet) == ["S001"]  # S002 not uploaded yet


def test_report_rolls_up_batch_status():
    sheet = parse_samplesheet(sheet_text("S001"))
    valid = {f: FileRecord(f, FileStatus.VALID, "S001") for f in sheet.expected_files}
    assert build_report("b1", valid, sheet).status == BatchStatus.READY_FOR_ANALYSIS

    one_bad = dict(valid)
    one_bad["S001_R2.fastq.gz"] = FileRecord("S001_R2.fastq.gz", FileStatus.QUARANTINED, "S001", "truncated")
    report = build_report("b1", one_bad, sheet)
    assert report.status == BatchStatus.NEEDS_ATTENTION
    assert report.attention[0]["reason"] == "truncated"

    partial = {"S001_R1.fastq.gz": valid["S001_R1.fastq.gz"]}
    report = build_report("b1", partial, sheet)
    assert report.counts["MISSING"] == 1


# ---------- FASTQ pair validation ----------

def test_valid_pair_and_md5_of_compressed_bytes():
    r1, r2 = pair()
    b1, b2 = fq(r1), fq(r2)
    res = validate_pair(io.BytesIO(b1), io.BytesIO(b2))
    assert res.ok, res.error
    assert res.r1.read_count == 3
    assert res.r1.md5 == hashlib.md5(b1).hexdigest()
    assert res.r2.md5 == hashlib.md5(b2).hexdigest()
    assert 0 < res.r1.gc_percent < 100


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda r1, r2: (r1, r2[:-1]), "read count mismatch"),
        (lambda r1, r2: ([(r1[0][0], "ACGT", "II")] + r1[1:], r2), "sequence length 4 != quality length 2"),
        (lambda r1, r2: ([(r1[0][0], "ACGX", "IIII")] + r1[1:], r2), "invalid base"),
        (lambda r1, r2: (r1, [("other 2:N", r2[0][1], r2[0][2])] + r2[1:]), "read names differ"),
    ],
)
def test_invalid_pairs_give_exact_reason(mutate, expected):
    r1, r2 = mutate(*pair())
    res = validate_pair(io.BytesIO(fq(r1)), io.BytesIO(fq(r2)))
    assert not res.ok
    assert expected in res.error


def test_truncated_gzip_is_caught():
    r1, r2 = pair(n=200, length=50)
    b1 = fq(r1)
    res = validate_pair(io.BytesIO(b1[: len(b1) // 2]), io.BytesIO(fq(r2)))
    assert not res.ok
    assert "corrupt or truncated" in res.error or "truncated" in res.error


def test_slash_style_read_names_pair_up():
    r1 = [("r1/1", "ACGT", "IIII")]
    r2 = [("r1/2", "TTGA", "IIII")]
    assert validate_pair(io.BytesIO(fq(r1)), io.BytesIO(fq(r2))).ok


# ---------- which file is at fault ----------

from intake_core import validation_outcome  # noqa: E402


def outcome_for(b1, b2, md5_1=None, md5_2=None):
    m1 = md5_1 or hashlib.md5(b1).hexdigest()
    m2 = md5_2 or hashlib.md5(b2).hexdigest()
    sheet = parse_samplesheet(f"sample_id,fastq_1,fastq_2,md5_1,md5_2\nS1,S1_R1.fastq.gz,S1_R2.fastq.gz,{m1},{m2}\n")
    result = validate_pair(io.BytesIO(b1), io.BytesIO(b2))
    return validation_outcome(sheet.samples["S1"], result)


def test_only_the_broken_file_is_quarantined():
    good_r2 = fq(pair(n=200, length=50)[1])
    truncated_r1 = fq(pair(n=200, length=50)[0])[:300]
    out = outcome_for(truncated_r1, good_r2)
    assert out["S1_R1.fastq.gz"][0] == FileStatus.QUARANTINED
    assert out["S1_R2.fastq.gz"] == (FileStatus.AWAITING_PAIR, None)


def test_pairing_problem_quarantines_both():
    r1, r2 = pair()
    out = outcome_for(fq(r1), fq(r2[:-1]))
    assert {s for s, _ in out.values()} == {FileStatus.QUARANTINED}


def test_md5_mismatch_names_the_file():
    out = outcome_for(fq(pair()[0]), fq(pair()[1]), md5_1="f" * 32)
    assert out["S1_R1.fastq.gz"][0] == FileStatus.QUARANTINED and "MD5 mismatch" in out["S1_R1.fastq.gz"][1]
    assert out["S1_R2.fastq.gz"][0] == FileStatus.AWAITING_PAIR
