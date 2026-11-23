"""Smoke tests for the two OCI Functions handlers, with the cloud layer faked."""

import importlib.util
import io
import json
import pathlib

import pytest

from intake_core.cloud import service

from test_service_flow import BATCH, SHEET, FakeStorage, FakeStore

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "functions" / name / "func.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Ctx:
    def __init__(self, method="GET", url="/"):
        self._m, self._u = method, url

    def Method(self): return self._m
    def RequestURL(self): return self._u
    def Headers(self): return {"X-Api-Key": self.key}
    key = "test-key"
    def SetResponseHeaders(self, headers, status_code): pass
    def GetResponseHeaders(self): return {}


@pytest.fixture
def api(monkeypatch):
    FakeStore.batches, FakeStore.files = {}, {}
    monkeypatch.setenv("INTAKE_API_KEY", "test-key")
    mod = load("intake-api")
    for target in (mod, service):
        monkeypatch.setattr(target, "Store", FakeStore, raising=False)
        monkeypatch.setattr(target, "Storage", FakeStorage, raising=False)
    monkeypatch.setattr(service.jobs, "launch_validation", lambda b, s: "ocid")
    monkeypatch.setattr(service, "notify", lambda *a: None)
    return mod


def call(mod, method, url, body=b""):
    resp = mod.handler(Ctx(method, url), io.BytesIO(body))
    return resp.status(), json.loads(resp.body())


def test_create_batch_then_samplesheet(api):
    status, body = call(api, "POST", "/v1/batches", b'{"lab_email": "lab@example.org"}')
    assert status == 201
    batch_id = body["batch_id"]
    status, body = call(api, "PUT", f"/v1/batches/{batch_id}/samplesheet", SHEET.encode())
    assert status == 200 and body["samples"] == 2


def test_bad_samplesheet_lists_problems(api):
    FakeStore().put_batch(BATCH, service.BatchStatus.OPEN)
    status, body = call(api, "PUT", f"/v1/batches/{BATCH}/samplesheet", b"sample_id\nS1\n")
    assert status == 422 and body["problems"]


def test_upload_rejects_bad_filenames_and_unknown_batch(api):
    FakeStore().put_batch(BATCH, service.BatchStatus.OPEN)
    status, body = call(api, "POST", f"/v1/batches/{BATCH}/uploads", b'{"files": ["../etc/passwd"]}')
    assert status == 400
    status, _ = call(api, "GET", "/v1/batches/99999999-2222-3333-4444-555555555555")
    assert status == 404


def test_rejects_missing_api_key(api, monkeypatch):
    monkeypatch.setattr(Ctx, "key", "wrong")
    status, _ = call(api, "POST", "/v1/batches", b"{}")
    assert status == 401


def test_event_handler_skips_non_upload_objects(monkeypatch):
    mod = load("on-object-created")
    event = {"eventType": "com.oraclecloud.objectstorage.createobject", "data": {"resourceName": "reports/x/report.json"}}
    resp = mod.handler(Ctx(), io.BytesIO(json.dumps(event).encode()))
    assert "skipped" in json.loads(resp.body())
