# Serverless functions

Two AWS Lambda functions (Python 3.12, standard library + boto3 only, so there is no packaging step). The
infrastructure is in [`infra/terraform/events.tf`](../infra/terraform/events.tf), and the design and alternatives
are in [ADR 0009](../docs/adr/0009-event-driven-intake-and-notifications.md).

| Function | Trigger | Does | Idempotency | Failure handling |
|---|---|---|---|---|
| [`intake`](intake/handler.py) | SQS ← EventBridge ← S3 `incoming/*.csv` | downloads the CSV, `POST /api/batches` with `source_ref = s3://bucket/key#etag` | `batches.source_ref` unique per tenant; a redelivery returns (and if needed starts) the existing batch | 5xx/timeouts: partial batch failure → SQS retry → DLQ after 3. 4xx: `rejected/<file>.error.json`, acknowledged. |
| [`notifier`](notifier/handler.py) | custom EventBridge bus (fed by the outbox relay) | formats `intervention.opened`, `incident.opened`, `task.failed`, `batch.finished` for Slack/Teams | DynamoDB conditional put on `tenant:event_id` (7-day TTL) | webhook error → claim released, exception → EventBridge retries for 1 h → DLQ |

Use it: drop a file at `s3://<intake_bucket output>/incoming/claims.csv`. Optional object metadata:
`x-amz-meta-credential-id`, `x-amz-meta-batch-name`, `x-amz-meta-max-concurrency`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q        # against moto's in-memory S3, Secrets Manager and DynamoDB
```

They cover: the source_ref and metadata wiring, partial batch failure reporting, 4xx versus 5xx classification,
the write-back of rejected files, a well-formed multipart body, notifier dedupe across redelivery, release of the
claim when sending fails, and routing.
