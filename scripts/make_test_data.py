#!/usr/bin/env python3
"""Generate a small synthetic batch: reference, paired FASTQs with known SNPs, sample sheet.

    python scripts/make_test_data.py --out testdata --samples 3 --with-problems

--with-problems adds the cases the intake service must catch:
  * a truncated R2 (validation fails, file quarantined)
  * a stray file not listed in the sample sheet (flagged UNEXPECTED)
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import pathlib
import random

COMPLEMENT = str.maketrans("ACGT", "TGCA")


def revcomp(s: str) -> str:
    return s.translate(COMPLEMENT)[::-1]


def simulate(genome: str, n_pairs: int, read_len: int, insert: int, rng: random.Random):
    for i in range(n_pairs):
        start = rng.randint(0, len(genome) - insert)
        frag = genome[start:start + insert]
        name = f"SIM:1:FC01:1:1101:{i // 1000}:{i % 1000}"
        yield name, frag[:read_len], revcomp(frag)[:read_len]


def write_fastq(path: pathlib.Path, records):
    with gzip.open(path, "wt", compresslevel=6) as fh:
        for name, seq in records:
            fh.write(f"@{name}\n{seq}\n+\n{'I' * len(seq)}\n")


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="testdata")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--genome-size", type=int, default=50_000)
    ap.add_argument("--pairs", type=int, default=20_000)
    ap.add_argument("--read-len", type=int, default=150)
    ap.add_argument("--with-problems", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out = pathlib.Path(args.out)
    (out / "fastq").mkdir(parents=True, exist_ok=True)

    reference = "".join(rng.choice("ACGT") for _ in range(args.genome_size))
    (out / "reference.fa").write_text(">chrT\n" + "\n".join(reference[i:i + 60] for i in range(0, len(reference), 60)) + "\n")

    rows, truth = [], []
    for n in range(1, args.samples + 1):
        sid = f"S{n:03d}"
        genome = list(reference)
        for pos in sorted(rng.sample(range(1000, args.genome_size - 1000), 10)):
            alt = rng.choice([b for b in "ACGT" if b != genome[pos]])
            truth.append((sid, pos + 1, genome[pos], alt))
            genome[pos] = alt
        pairs = list(simulate("".join(genome), args.pairs, args.read_len, 350, rng))
        r1, r2 = out / "fastq" / f"{sid}_R1.fastq.gz", out / "fastq" / f"{sid}_R2.fastq.gz"
        write_fastq(r1, ((nm + " 1:N:0:ACGT", a) for nm, a, _ in pairs))
        write_fastq(r2, ((nm + " 2:N:0:ACGT", b) for nm, _, b in pairs))
        rows.append([sid, r1.name, r2.name, md5(r1), md5(r2)])  # checksums of the intact files

    if args.with_problems and args.samples >= 2:
        bad = out / "fastq" / "S002_R2.fastq.gz"
        data = bad.read_bytes()
        bad.write_bytes(data[: len(data) // 2])  # simulate an interrupted transfer
        write_fastq(out / "fastq" / "stray_sample_R1.fastq.gz", [("x 1:N:0:A", "ACGT")])

    with open(out / "samplesheet.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "fastq_1", "fastq_2", "md5_1", "md5_2"])
        w.writerows(rows)
    with open(out / "truth_snps.tsv", "w") as fh:
        fh.write("sample_id\tpos\tref\talt\n")
        fh.writelines(f"{s}\t{p}\t{r}\t{a}\n" for s, p, r, a in truth)

    print(f"wrote {args.samples} samples to {out}/ (sample sheet, reference, truth_snps.tsv)")


if __name__ == "__main__":
    main()
