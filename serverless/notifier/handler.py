"""Notifier Lambda: turns PortalPilot domain events into chat messages for the client's operations channel.

    outbox relay ──PutEvents──▶ custom bus `pp-<client>` ──rule (types below)──▶ this function ──▶ Slack/Teams webhook
                                                              └── retries exhausted ──▶ DLQ (alarmed)

EventBridge delivers at least once, and the relay can republish after a crash, so each `event_id` is claimed in
DynamoDB with a conditional put before anything is sent (TTL 7 days). A failure after the claim releases it, so the
retry can send.

Events handled: intervention.opened (someone must act), incident.opened (alert fired), task.failed, batch.finished.

Configuration (environment):
    WEBHOOK_SECRET_ARN   Secrets Manager secret whose SecretString is the incoming-webhook URL (empty: log only)
    DEDUPE_TABLE         DynamoDB table, partition key `event_id` (string), TTL attribute `expires_at`
    STUDIO_URL           base URL of the operator Studio, for deep links
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

_ddb = None
_webhook: str | None = None


def ddb():
    global _ddb
    if _ddb is None:
        _ddb = boto3.client("dynamodb")
    return _ddb


def webhook_url() -> str:
    global _webhook
    if _webhook is None:
        arn = os.environ.get("WEBHOOK_SECRET_ARN", "")
        _webhook = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"].strip() if arn else ""
    return _webhook


def render(detail: dict) -> str | None:
    """Message text, or None for events this channel does not want."""
    t, d = detail["type"], detail.get("data", {})
    studio = os.environ.get("STUDIO_URL", "").rstrip("/")
    task_link = f"{studio}/#/tasks/{detail['task_id']}" if detail.get("task_id") else ""
    if t == "intervention.opened":
        kind = (d.get("kind") or "input").replace("_", " ")
        return f":raising_hand: *{kind}* needed at stage `{d.get('stage', '?')}`: {d.get('prompt', '')}\n{task_link}"
    if t == "incident.opened":
        icon = ":rotating_light:" if d.get("severity") == "page" else ":warning:"
        return f"{icon} Incident *{d.get('alertname', 'alert')}* ({d.get('severity', 'ticket')})\n{studio}/#/incidents/{d.get('id', '')}"
    if t == "task.failed":
        return f":x: Claim task failed: {d.get('error', 'no detail')}\n{task_link}"
    if t == "batch.finished":
        return f":white_check_mark: Batch finished: *{d.get('status', '?')}*\n{studio}/#/batches/{detail.get('batch_id', '')}"
    return None


def claim(event_id: str) -> bool:
    try:
        ddb().put_item(TableName=os.environ["DEDUPE_TABLE"],
                       Item={"event_id": {"S": event_id}, "expires_at": {"N": str(int(time.time()) + 7 * 86400)}},
                       ConditionExpression="attribute_not_exists(event_id)")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def release(event_id: str) -> None:
    ddb().delete_item(TableName=os.environ["DEDUPE_TABLE"], Key={"event_id": {"S": event_id}})


def send(text: str) -> None:
    url = webhook_url()
    if not url:
        log.info(json.dumps({"msg": "notify (no webhook configured)", "text": text}))
        return
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        if r.status >= 300:
            raise RuntimeError(f"webhook answered {r.status}")


def handler(event: dict, context: Any = None) -> dict:
    detail = event["detail"]
    text = render(detail)
    if text is None:
        return {"sent": False, "reason": "not routed"}
    eid = f"{detail['tenant']}:{detail['event_id']}"
    if not claim(eid):
        log.info(json.dumps({"msg": "duplicate", "event_id": eid}))
        return {"sent": False, "reason": "duplicate"}
    try:
        send(text)
    except Exception:
        release(eid)  # let EventBridge's retry send it
        raise
    return {"sent": True}
