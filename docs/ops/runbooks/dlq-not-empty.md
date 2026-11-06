# Intake or notifier DLQ not empty (CloudWatch alarm)

**Signal:** `ApproximateNumberOfMessagesVisible > 0` on `pp-<client>-intake-dlq` or `pp-<client>-notify-dlq`.

## Intake DLQ: a dropped CSV did not become a batch after 3 attempts

1. Read a message: `aws sqs receive-message --queue-url <dlq url> --max-number-of-messages 1`. The body is the S3
   event and names the file.
2. Logs `/aws/lambda/pp-<client>-intake`, `"msg": "retry"` lines: the API answered 5xx or timed out.
3. Fix the cause (usually [api-errors](api-errors.md)), then **redrive**: SQS console → DLQ → *Start DLQ redrive*.
   This is safe: `source_ref` makes a redelivered file return the existing batch, and row-level idempotency keys
   protect the rows.
4. Files the API rejected with 4xx never reach the DLQ. They appear as `rejected/<name>.error.json` in the intake
   bucket, for the client to fix and re-drop.

## Notifier DLQ: a message could not be posted to chat for an hour

1. Logs `/aws/lambda/pp-<client>-notifier`: usually the webhook URL was revoked (403/404).
2. Update it: `terraform apply -var notify_webhook_url=...` (stored in Secrets Manager). The function caches the URL
   per container, so the next cold start picks it up. To force one, publish a new version or change an env var.
3. The messages in the DLQ are stale by now. Read them to see who missed what, then purge. Anything still open is
   visible in the Studio anyway.
