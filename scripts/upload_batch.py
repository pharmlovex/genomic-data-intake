#!/usr/bin/env python3
"""Act as the lab: create a batch, upload FASTQs, then (optionally late) the sample sheet.

    export INTAKE_API=https://<gateway-host>/v1 INTAKE_API_KEY=...
    python scripts/upload_batch.py testdata --samplesheet-last
    python scripts/upload_batch.py --status <batch_id>

Standard library only. Each file goes straight to Object Storage through its
own write-only pre-authenticated request URL; the API never sees the data.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import socket
import time
import urllib.error
import urllib.request

API = os.environ.get("INTAKE_API", "").rstrip("/")
KEY = os.environ.get("INTAKE_API_KEY", "")

# A batch id that is well-formed but never issued, so the warm-up GET changes nothing.
WARMUP_PATH = "/batches/00000000-0000-0000-0000-000000000000"
WARMUP_ATTEMPTS = 4
WARMUP_TIMEOUT_S = 90


def warm_up() -> None:
    """Wake the intake-api function before doing anything that is not safe to retry.

    A function that has been idle cold-starts: OCI pulls the image and attaches it to
    the private subnet, which can take over a minute. This read-only GET absorbs that
    delay. Any 4xx means the function answered (it is warm); timeouts, connection
    errors and gateway 5xx responses mean it is still starting, so try again.
    """
    req = urllib.request.Request(f"{API}{WARMUP_PATH}", method="GET", headers={"X-Api-Key": KEY})
    for attempt in range(1, WARMUP_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=WARMUP_TIMEOUT_S):
                return
        except urllib.error.HTTPError as err:
            if err.code == 401:
                sys.exit("API rejected the key: check INTAKE_API_KEY")
            if err.code < 500:
                return
            reason = f"HTTP {err.code}"
        except (TimeoutError, socket.timeout, urllib.error.URLError) as err:
            reason = getattr(err, "reason", None) or type(err).__name__
        waited = time.monotonic() - started
        if attempt == WARMUP_ATTEMPTS:
            sys.exit(f"API still not responding after {WARMUP_ATTEMPTS} attempts ({reason})")
        print(f"API is starting up ({reason} after {waited:.0f} s), retrying "
              f"[{attempt}/{WARMUP_ATTEMPTS - 1}]...", file=sys.stderr)
        time.sleep(5)


def call(method: str, path: str, body: bytes | None = None, content_type="application/json"):
    req = urllib.request.Request(f"{API}{path}", data=body, method=method,
                                 headers={"X-Api-Key": KEY, "Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        sys.exit(f"{method} {path} -> {err.code}: {err.read().decode()}")


def put_file(url: str, path: pathlib.Path) -> None:
    size = path.stat().st_size
    with open(path, "rb") as fh:  # streamed, not read into memory
        req = urllib.request.Request(url, data=fh, method="PUT", headers={
            "Content-Length": str(size), "Content-Type": "application/octet-stream"})
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=3600):
            pass
    print(f"  uploaded {path.name} ({size / 1e6:.1f} MB in {time.monotonic() - started:.1f} s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", help="output of make_test_data.py")
    ap.add_argument("--samplesheet-last", action="store_true",
                    help="upload FASTQs first, sample sheet afterwards (the real-world case)")
    ap.add_argument("--status", metavar="BATCH_ID", help="just print a batch report")
    args = ap.parse_args()
    if not API or not KEY:
        sys.exit("set INTAKE_API and INTAKE_API_KEY")

    warm_up()

    if args.status:
        print(json.dumps(call("GET", f"/batches/{args.status}"), indent=2))
        return

    folder = pathlib.Path(args.folder)
    sheet = (folder / "samplesheet.csv").read_bytes()
    fastqs = sorted((folder / "fastq").glob("*.fastq.gz"))

    batch_id = call("POST", "/batches", json.dumps({"lab_email": "lab@example.org"}).encode())["batch_id"]
    print(f"batch {batch_id}")

    def send_sheet():
        result = call("PUT", f"/batches/{batch_id}/samplesheet", sheet, "text/csv")
        print(f"sample sheet accepted: {result['samples']} samples, validation launched for {result['launched']}")

    if not args.samplesheet_last:
        send_sheet()
    urls = call("POST", f"/batches/{batch_id}/uploads", json.dumps({"files": [p.name for p in fastqs]}).encode())
    for item, path in zip(urls["uploads"], fastqs):
        put_file(item["url"], path)
    if args.samplesheet_last:
        time.sleep(5)  # let the object events land, to show files staged as AWAITING_SAMPLESHEET
        print(json.dumps(call("GET", f"/batches/{batch_id}")["counts"]))
        send_sheet()

    print(f"\nwatch it: python scripts/upload_batch.py --status {batch_id}")


if __name__ == "__main__":
    main()
