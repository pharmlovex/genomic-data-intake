"""Intake API, served through OCI API Gateway.

Routes (all JSON):
  POST /batches                          {"lab_email": "..."}            -> {"batch_id": ...}
  PUT  /batches/{id}/samplesheet         body: the CSV text               -> reconcile + launch
  POST /batches/{id}/uploads             {"files": ["S001_R1.fastq.gz"]}  -> one upload URL per file
  GET  /batches/{id}                     -> the batch report

The API never handles sequencing data itself: it hands out short-lived,
write-only upload URLs and the bytes go straight to Object Storage.
"""

import hmac
import io
import json
import logging
import os
import re
import uuid
from urllib.parse import urlparse

from fdk import response

from intake_core import BatchStatus, SampleSheetError
from intake_core.cloud.service import on_samplesheet, publish_report
from intake_core.cloud.storage import Storage
from intake_core.cloud.store import Store

log = logging.getLogger("intake-api")
FILENAME = re.compile(r"^[A-Za-z0-9._-]+\.f(ast)?q\.gz$")
MAX_FILES_PER_CALL = 200


def _reply(ctx, status, body):
    return response.Response(ctx, response_data=json.dumps(body), status_code=status,
                             headers={"Content-Type": "application/json"})


def _route(ctx):
    url = ctx.RequestURL() or ctx.Headers().get("fn-http-request-url", "/")
    method = (ctx.Method() or ctx.Headers().get("fn-http-method", "GET")).upper()
    parts = [p for p in urlparse(url).path.split("/") if p]
    # Gateway path prefix (e.g. /v1) is stripped by taking everything from "batches".
    if "batches" in parts:
        parts = parts[parts.index("batches"):]
    return method, parts


def _authorised(ctx) -> bool:
    """Shared-key check. A production setup would use an API Gateway authorizer or OCI IAM."""
    expected = os.environ.get("INTAKE_API_KEY", "")
    headers = {k.lower(): v for k, v in (ctx.Headers() or {}).items()}
    supplied = headers.get("x-api-key", "")
    if isinstance(supplied, list):
        supplied = supplied[0] if supplied else ""
    return bool(expected) and hmac.compare_digest(supplied, expected)


def handler(ctx, data: io.BytesIO = None):
    if not _authorised(ctx):
        return _reply(ctx, 401, {"error": "missing or invalid X-Api-Key"})
    method, parts = _route(ctx)
    raw = data.getvalue() if data else b""
    try:
        if method == "POST" and parts == ["batches"]:
            body = json.loads(raw or b"{}")
            batch_id = str(uuid.uuid4())
            Store().put_batch(batch_id, BatchStatus.OPEN, lab_email=str(body.get("lab_email", ""))[:254])
            return _reply(ctx, 201, {"batch_id": batch_id, "status": BatchStatus.OPEN.value})

        if len(parts) >= 2 and parts[0] == "batches":
            batch_id = parts[1]
            store = Store()
            if store.get_batch(batch_id) is None:
                return _reply(ctx, 404, {"error": "batch not found"})

            if method == "PUT" and parts[2:] == ["samplesheet"]:
                result = on_samplesheet(batch_id, raw.decode("utf-8-sig"))
                return _reply(ctx, 200, result)

            if method == "POST" and parts[2:] == ["uploads"]:
                files = json.loads(raw or b"{}").get("files", [])
                bad = [f for f in files if not isinstance(f, str) or not FILENAME.match(f)]
                if not files or bad or len(files) > MAX_FILES_PER_CALL:
                    return _reply(ctx, 400, {"error": "give 1-200 .fastq.gz filenames", "invalid": bad})
                storage = Storage()
                return _reply(ctx, 200, {"uploads": [storage.upload_url(batch_id, f) for f in files]})

            if method == "GET" and parts[2:] == []:
                return _reply(ctx, 200, publish_report(store, batch_id))

        return _reply(ctx, 404, {"error": f"no route for {method} /{'/'.join(parts)}"})
    except SampleSheetError as exc:
        return _reply(ctx, 422, {"error": "sample sheet rejected", "problems": exc.problems})
    except (ValueError, json.JSONDecodeError) as exc:
        return _reply(ctx, 400, {"error": str(exc)})
    except Exception:
        log.exception("unhandled error")
        return _reply(ctx, 500, {"error": "internal error"})
