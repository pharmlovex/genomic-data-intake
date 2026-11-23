"""Triggered by an OCI Events rule on 'Object - Create' in the intake bucket.

It only records the arrival and decides what happens next: stage the file
(no sample sheet yet), flag it (not in the sample sheet), or launch a
validation job once both mates of a sample are present. It never reads the
file itself, so it finishes in well under a second whatever the file size.
"""

import io
import json
import logging

from fdk import response

from intake_core.cloud.service import on_file_arrived
from intake_core.cloud.storage import parse_raw_key

log = logging.getLogger("on-object-created")


def handler(ctx, data: io.BytesIO = None):
    event = json.loads(data.getvalue() or b"{}")
    key = (event.get("data") or {}).get("resourceName", "")
    parsed = parse_raw_key(key)
    if parsed is None:  # reports/, quarantine/, results/ etc. are not uploads
        return response.Response(ctx, response_data=json.dumps({"skipped": key}))
    batch_id, filename = parsed
    result = on_file_arrived(batch_id, filename)
    log.info("arrival %s -> %s", key, result)
    return response.Response(ctx, response_data=json.dumps(result), headers={"Content-Type": "application/json"})
