"""Object Storage helpers.

Bucket layout:
  raw/<batch_id>/<filename>          uploads from the lab (via pre-authenticated requests)
  quarantine/<batch_id>/<filename>   files that failed validation
  samplesheets/<batch_id>.csv        copy of the accepted sample sheet (pipeline input)
  reports/<batch_id>/report.json     the lab-facing status report
  results/<batch_id>/...             pipeline outputs (VCFs, QC)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import client, settings

RAW = "raw"


def raw_key(batch_id: str, filename: str) -> str:
    return f"{RAW}/{batch_id}/{filename}"


def parse_raw_key(key: str) -> tuple[str, str] | None:
    """'raw/<batch>/<file>' -> (batch, file); anything else -> None."""
    parts = key.split("/")
    if len(parts) == 3 and parts[0] == RAW and parts[1] and parts[2]:
        return parts[1], parts[2]
    return None


class Storage:
    def __init__(self):
        import oci

        self._oci = oci
        self._os = client(oci.object_storage.ObjectStorageClient)
        self._s = settings()

    def upload_url(self, batch_id: str, filename: str) -> dict:
        """A write-only pre-authenticated request for exactly one object.

        The lab uploads straight to Object Storage with it, so 100 GB files never
        pass through the API or a function. A single PUT handles objects up to
        50 GiB; larger files need a multipart upload (see README, next steps).
        """
        expires = datetime.now(timezone.utc) + timedelta(hours=self._s.par_ttl_hours)
        details = self._oci.object_storage.models.CreatePreauthenticatedRequestDetails(
            name=f"upload-{batch_id}-{filename}"[:255],
            object_name=raw_key(batch_id, filename),
            access_type="ObjectWrite",
            time_expires=expires,
        )
        par = self._os.create_preauthenticated_request(self._s.namespace, self._s.bucket, details).data
        return {"filename": filename, "url": par.full_path, "expires": expires.isoformat(timespec="seconds")}

    def open_stream(self, key: str):
        """A streaming, file-like handle on an object (nothing is buffered whole)."""
        resp = self._os.get_object(self._s.namespace, self._s.bucket, key)
        return resp.data.raw

    def quarantine(self, batch_id: str, filename: str) -> None:
        """Move a failed file out of raw/ so the pipeline can never pick it up."""
        src = raw_key(batch_id, filename)
        dst = f"quarantine/{batch_id}/{filename}"
        details = self._oci.object_storage.models.RenameObjectDetails(source_name=src, new_name=dst)
        self._os.rename_object(self._s.namespace, self._s.bucket, details)

    def write_samplesheet(self, batch_id: str, csv_text: str) -> str:
        """Keep a copy next to the data: the Nextflow pipeline reads it from here."""
        key = f"samplesheets/{batch_id}.csv"
        self._os.put_object(self._s.namespace, self._s.bucket, key, csv_text.encode(), content_type="text/csv")
        return key

    def write_report(self, batch_id: str, report: dict) -> str:
        key = f"reports/{batch_id}/report.json"
        self._os.put_object(self._s.namespace, self._s.bucket, key, json.dumps(report, indent=2).encode(),
                            content_type="application/json")
        return key
