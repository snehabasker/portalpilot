"""Intake Lambda: a CSV dropped into the intake bucket becomes a PortalPilot batch.

    S3 (incoming/…csv) ──Object Created──▶ EventBridge rule ──▶ SQS intake queue ──▶ this function ──▶ POST /api/batches
                                                                     └── after 3 failed receives ──▶ DLQ (alarmed)

Why SQS between EventBridge and the Lambda: it buffers bursts (a client drops 40 files at once), gives per-message
retries with a dead-letter queue, and lets the function report partial batch failures so one bad file does not
re-run the others.

Idempotency: the API receives `source_ref = s3://bucket/key#etag`, which is unique per tenant. A redelivered event
returns the existing batch (and starts it if the first attempt died before doing so). Re-uploading the same key with
different content has a new ETag, so it is a new batch, and row-level idempotency keys still stop rows that were
already filed.

Permanent failures (the API answers 4xx: bad CSV, unknown credential) are not retried. The function writes
`rejected/<key>.error.json` next to the file so the client can see why, and acknowledges the message.

Configuration (environment):
    API_URL                 base URL of the PortalPilot API
    APP_SECRET_ARN          Secrets Manager secret holding OPERATOR_TOKEN (JSON)
    DEFAULT_CREDENTIAL_ID   portal login to use unless the object has x-amz-meta-credential-id
    APPROVAL_THRESHOLD      optional; amounts above it need a human approval
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = None
_token: str | None = None


class Permanent(Exception):
    """The API rejected the file; retrying will not help."""


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def operator_token() -> str:
    global _token
    if _token is None:
        sm = boto3.client("secretsmanager")
        _token = json.loads(sm.get_secret_value(SecretId=os.environ["APP_SECRET_ARN"])["SecretString"])["OPERATOR_TOKEN"]
    return _token


def multipart(fields: dict[str, str], file_field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f"Content-Type: text/csv\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post_batch(fields: dict[str, str], filename: str, content: bytes) -> dict:
    body, ctype = multipart(fields, "file", filename, content)
    req = urllib.request.Request(os.environ["API_URL"].rstrip("/") + "/api/batches", data=body, method="POST",
                                 headers={"Content-Type": ctype, "Authorization": f"Bearer {operator_token()}",
                                          "X-Operator": "intake-lambda"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:2000]
        if 400 <= e.code < 500 and e.code not in (408, 409, 429):
            raise Permanent(f"API {e.code}: {detail}") from e
        raise RuntimeError(f"API {e.code}: {detail}") from e


def s3_object_from_event(evt: dict) -> tuple[str, str, str | None]:
    d = evt["detail"]
    return d["bucket"]["name"], urllib.parse.unquote_plus(d["object"]["key"]), d["object"].get("etag")


def process(evt: dict) -> dict:
    bucket, key, etag = s3_object_from_event(evt)
    obj = s3().get_object(Bucket=bucket, Key=key)
    etag = etag or obj["ETag"].strip('"')
    meta = obj.get("Metadata", {})
    fields = {
        "name": meta.get("batch-name") or key.rsplit("/", 1)[-1],
        "credential_id": meta.get("credential-id") or os.environ["DEFAULT_CREDENTIAL_ID"],
        "source_ref": f"s3://{bucket}/{key}#{etag}",
        "max_concurrency": meta.get("max-concurrency", "3"),
    }
    if os.environ.get("APPROVAL_THRESHOLD"):
        fields["approval_threshold"] = os.environ["APPROVAL_THRESHOLD"]
    try:
        out = post_batch(fields, key.rsplit("/", 1)[-1], obj["Body"].read())
    except Permanent as e:
        s3().put_object(Bucket=bucket, Key=f"rejected/{key.removeprefix('incoming/')}.error.json",
                        Body=json.dumps({"source": key, "etag": etag, "error": str(e)}).encode(),
                        ContentType="application/json")
        log.warning(json.dumps({"msg": "rejected", "key": key, "error": str(e)}))
        return {"key": key, "rejected": True}
    log.info(json.dumps({"msg": "batch", "key": key, **out}))
    return {"key": key, **out}


def handler(event: dict, context: Any = None) -> dict:
    """SQS event source with ReportBatchItemFailures: only the failed messages return to the queue."""
    failures = []
    for rec in event.get("Records", []):
        try:
            process(json.loads(rec["body"]))
        except Exception as e:  # transient: let SQS redeliver (and dead-letter after maxReceiveCount)
            log.error(json.dumps({"msg": "retry", "message_id": rec.get("messageId"), "error": str(e)[:500]}))
            failures.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": failures}
