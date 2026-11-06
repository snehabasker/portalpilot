-- 002: incident response and event-driven intake.
--
--   incidents / incident_events   alerts from Alertmanager become incidents with a timeline (ack, notes, resolve)
--   outbox_cursor                 how far the outbox relay has published task_events to the event bus
--   batches.source_ref            the S3 object (bucket/key#etag) a batch was created from, unique per tenant,
--                                 so a redelivered "object created" event cannot start the same batch twice

-- ---------------------------------------------------------------- incidents
CREATE TYPE incident_status   AS ENUM ('firing', 'acknowledged', 'resolved');
CREATE TYPE incident_severity AS ENUM ('page', 'ticket');

CREATE TABLE incidents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint     text NOT NULL,                 -- Alertmanager's alert fingerprint (alertname + labels)
    alertname       text NOT NULL,
    severity        incident_severity NOT NULL,
    status          incident_status NOT NULL DEFAULT 'firing',
    summary         text NOT NULL,
    runbook_url     text,
    labels          jsonb NOT NULL DEFAULT '{}',
    started_at      timestamptz NOT NULL,
    acknowledged_at timestamptz,
    acknowledged_by text,
    resolved_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);
-- one open incident per alert fingerprint; a re-fire after resolution opens a new one
CREATE UNIQUE INDEX incidents_one_open ON incidents (fingerprint) WHERE status <> 'resolved';
CREATE INDEX incidents_recent ON incidents (created_at DESC);

CREATE TABLE incident_events (
    id          bigserial PRIMARY KEY,
    incident_id uuid NOT NULL REFERENCES incidents(id),
    kind        text NOT NULL,                     -- fired | refired | acknowledged | note | resolved | auto_resolved
    actor       text NOT NULL,                     -- alertmanager | operator:<name>
    body        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX incident_events_by_incident ON incident_events (incident_id, id);

-- ---------------------------------------------------------------- outbox relay
-- task_events is written by every state change (from activities that retry until they succeed), so it doubles as
-- an outbox. The relay publishes rows with id > last_id and then advances the cursor: at-least-once delivery, and
-- consumers dedupe on the event id.
CREATE TABLE outbox_cursor (
    name       text PRIMARY KEY,
    last_id    bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- idempotent intake
ALTER TABLE batches ADD COLUMN source_ref text;
CREATE UNIQUE INDEX batches_source_ref ON batches (tenant_id, source_ref) WHERE source_ref IS NOT NULL;
