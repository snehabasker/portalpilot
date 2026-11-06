"""Lambda handlers against moto's in-memory AWS. HTTP calls to the PortalPilot API and to the chat webhook are
replaced by fakes, so these tests check the event wiring, idempotency and failure routing, not the network."""
from __future__ import annotations

import importlib
import json
import os
import sys
import urllib.error
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    sys.path.insert(0, str(ROOT / name))
    try:
        sys.modules.pop("handler", None)
        return importlib.import_module("handler")
    finally:
        sys.path.pop(0)


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": "eu-north-1"}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        yield


def s3_event(bucket: str, key: str, etag: str = "abc123") -> dict:
    # what EventBridge delivers for an S3 "Object Created" notification, wrapped in an SQS record
    evt = {"source": "aws.s3", "detail-type": "Object Created",
           "detail": {"bucket": {"name": bucket}, "object": {"key": key, "etag": etag}}}
    return {"messageId": f"m-{key}", "body": json.dumps(evt)}


# ------------------------------------------------------------------------------------------- intake
@pytest.fixture
def intake(monkeypatch):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="intake", CreateBucketConfiguration={"LocationConstraint": "eu-north-1"})
    arn = boto3.client("secretsmanager").create_secret(
        Name="app", SecretString=json.dumps({"OPERATOR_TOKEN": "tok"}))["ARN"]
    monkeypatch.setenv("API_URL", "http://api.test")
    monkeypatch.setenv("APP_SECRET_ARN", arn)
    monkeypatch.setenv("DEFAULT_CREDENTIAL_ID", "cred-1")
    h = load("intake")
    calls: list[dict] = []
    return h, s3, calls


def test_intake_posts_batch_with_source_ref(intake, monkeypatch):
    h, s3, calls = intake
    s3.put_object(Bucket="intake", Key="incoming/week 45.csv", Body=b"Pol #,DOL\nP1,2026-01-01\n",
                  Metadata={"credential-id": "cred-9"})
    monkeypatch.setattr(h, "post_batch", lambda f, fn, c: calls.append({**f, "filename": fn, "body": c}) or
                        {"id": "b1", "runnable": 1})
    out = h.handler({"Records": [s3_event("intake", "incoming/week+45.csv", "e1")]})
    assert out == {"batchItemFailures": []}
    assert calls[0]["source_ref"] == "s3://intake/incoming/week 45.csv#e1"   # key is URL-decoded
    assert calls[0]["credential_id"] == "cred-9"                            # object metadata wins over the default
    assert calls[0]["filename"] == "week 45.csv" and calls[0]["body"].startswith(b"Pol #")


def test_intake_reports_only_failed_messages(intake, monkeypatch):
    h, s3, _ = intake
    s3.put_object(Bucket="intake", Key="incoming/a.csv", Body=b"x")
    s3.put_object(Bucket="intake", Key="incoming/b.csv", Body=b"x")

    def flaky(fields, fn, content):
        if fn == "b.csv":
            raise RuntimeError("API 503")
        return {"id": "b1"}
    monkeypatch.setattr(h, "post_batch", flaky)
    out = h.handler({"Records": [s3_event("intake", "incoming/a.csv"), s3_event("intake", "incoming/b.csv")]})
    assert out == {"batchItemFailures": [{"itemIdentifier": "m-incoming/b.csv"}]}


def test_intake_permanent_error_is_written_back_not_retried(intake, monkeypatch):
    h, s3, _ = intake
    s3.put_object(Bucket="intake", Key="incoming/bad.csv", Body=b"x")

    def reject(*a):
        raise h.Permanent("API 422: credential_id is not a uuid")
    monkeypatch.setattr(h, "post_batch", reject)
    out = h.handler({"Records": [s3_event("intake", "incoming/bad.csv")]})
    assert out == {"batchItemFailures": []}
    err = json.loads(s3.get_object(Bucket="intake", Key="rejected/bad.csv.error.json")["Body"].read())
    assert "422" in err["error"] and err["source"] == "incoming/bad.csv"


def test_intake_classifies_http_errors(intake, monkeypatch):
    h, _, _ = intake

    def raising(code):
        def urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return urlopen
    for code, exc in ((422, h.Permanent), (429, RuntimeError), (503, RuntimeError)):
        monkeypatch.setattr(h.urllib.request, "urlopen", raising(code))
        with pytest.raises(exc):
            h.post_batch({"name": "n"}, "f.csv", b"x")


def test_multipart_body_is_well_formed(intake):
    h, _, _ = intake
    body, ctype = h.multipart({"name": "n", "source_ref": "s3://b/k#e"}, "file", "f.csv", b"a,b\n1,2\n")
    boundary = ctype.split("boundary=")[1]
    assert body.count(f"--{boundary}".encode()) == 4 and body.endswith(f"--{boundary}--\r\n".encode())
    assert b'name="source_ref"\r\n\r\ns3://b/k#e' in body and b"a,b\n1,2\n" in body


# ------------------------------------------------------------------------------------------- notifier
@pytest.fixture
def notifier(monkeypatch):
    boto3.client("dynamodb").create_table(
        TableName="dedupe", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "event_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "event_id", "AttributeType": "S"}])
    monkeypatch.setenv("DEDUPE_TABLE", "dedupe")
    monkeypatch.setenv("STUDIO_URL", "https://studio.example")
    monkeypatch.setenv("WEBHOOK_SECRET_ARN", "")
    h = load("notifier")
    sent: list[str] = []
    monkeypatch.setattr(h, "send", sent.append)
    return h, sent


def bus_event(type_: str, event_id: int = 1, **data) -> dict:
    return {"detail-type": type_, "source": "portalpilot",
            "detail": {"event_id": event_id, "type": type_, "tenant": "demo", "task_id": "t1", "batch_id": "b1",
                       "occurred_at": "2026-11-06T10:00:00+00:00", "data": data}}


def test_notifier_sends_once_per_event_id(notifier):
    h, sent = notifier
    evt = bus_event("intervention.opened", 7, kind="mfa_code", stage="login", prompt="Enter the code")
    assert h.handler(evt) == {"sent": True}
    assert h.handler(evt) == {"sent": False, "reason": "duplicate"}   # EventBridge redelivery
    assert len(sent) == 1 and "mfa code" in sent[0] and "https://studio.example/#/tasks/t1" in sent[0]


def test_notifier_releases_claim_when_send_fails(notifier, monkeypatch):
    h, sent = notifier
    evt = bus_event("task.failed", 8, error="BadCredentials")

    def boom(text):
        raise RuntimeError("webhook down")
    monkeypatch.setattr(h, "send", boom)
    with pytest.raises(RuntimeError):
        h.handler(evt)
    monkeypatch.setattr(h, "send", sent.append)
    assert h.handler(evt) == {"sent": True}                               # the retry is not swallowed as a duplicate


def test_notifier_ignores_unrouted_types(notifier):
    h, sent = notifier
    assert h.handler(bus_event("task.succeeded", 9))["reason"] == "not routed"
    assert sent == []


def test_incident_message_links_incident(notifier):
    h, sent = notifier
    h.handler(bus_event("incident.opened", 10, id="i-1", alertname="DoubleSubmitSuspected", severity="page"))
    assert ":rotating_light:" in sent[0] and "/#/incidents/i-1" in sent[0]
