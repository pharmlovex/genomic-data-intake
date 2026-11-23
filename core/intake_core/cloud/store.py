"""State store on OCI NoSQL Database (Always Free eligible).

Tables (created by Terraform):
  intake_batches  PRIMARY KEY(batch_id)
  intake_files    PRIMARY KEY(SHARD(batch_id), filename)   -> one shard per batch,
                  so "all files in a batch" is a single-shard query.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..reconcile import FileRecord
from ..samplesheet import SampleSheet, parse_samplesheet
from ..status import BatchStatus, FileStatus
from . import client, settings

_BATCH_ID = re.compile(r"^[a-f0-9-]{36}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_batch_id(batch_id: str) -> str:
    if not _BATCH_ID.match(batch_id):
        raise ValueError(f"invalid batch id {batch_id!r}")
    return batch_id


class Store:
    def __init__(self):
        import oci

        self._oci = oci
        self._nosql = client(oci.nosql.NosqlClient)
        self._s = settings()

    # ----- batches -----
    def put_batch(self, batch_id: str, status: BatchStatus, lab_email: str = "", samplesheet_csv: str = "",
                  created_at: str | None = None) -> None:
        value = {
            "batch_id": check_batch_id(batch_id),
            "status": status.value,
            "lab_email": lab_email,
            "samplesheet_csv": samplesheet_csv,
            "created_at": created_at or _now(),
            "updated_at": _now(),
        }
        self._put(self._s.batches_table, value)

    def get_batch(self, batch_id: str) -> dict | None:
        resp = self._nosql.get_row(
            self._s.batches_table, key=[f"batch_id:{check_batch_id(batch_id)}"], compartment_id=self._s.compartment_id
        )
        return resp.data.value or None

    def get_samplesheet(self, batch: dict) -> SampleSheet | None:
        text = (batch or {}).get("samplesheet_csv") or ""
        return parse_samplesheet(text) if text else None

    def set_batch_status(self, batch: dict, status: BatchStatus) -> None:
        if batch.get("status") == status.value:
            return
        self.put_batch(batch["batch_id"], status, batch.get("lab_email", ""), batch.get("samplesheet_csv", ""),
                       batch.get("created_at"))

    # ----- files -----
    def put_file(self, batch_id: str, rec: FileRecord) -> None:
        self._put(self._s.files_table, {
            "batch_id": check_batch_id(batch_id),
            "filename": rec.filename,
            "status": rec.status.value,
            "sample_id": rec.sample_id,
            "reason": rec.reason,
            "md5": rec.md5,
            "read_count": rec.read_count,
            "updated_at": _now(),
        })

    def list_files(self, batch_id: str) -> dict[str, FileRecord]:
        statement = f"SELECT * FROM {self._s.files_table} WHERE batch_id = '{check_batch_id(batch_id)}'"
        details = self._oci.nosql.models.QueryDetails(compartment_id=self._s.compartment_id, statement=statement)
        rows = self._oci.pagination.list_call_get_all_results(self._nosql.query, details).data
        out = {}
        for row in rows:
            out[row["filename"]] = FileRecord(
                filename=row["filename"], status=FileStatus(row["status"]), sample_id=row.get("sample_id"),
                reason=row.get("reason"), md5=row.get("md5"), read_count=row.get("read_count"),
            )
        return out

    def _put(self, table: str, value: dict) -> None:
        details = self._oci.nosql.models.UpdateRowDetails(compartment_id=self._s.compartment_id, value=value)
        self._nosql.update_row(table, details)
