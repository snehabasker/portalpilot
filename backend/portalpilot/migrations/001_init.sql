-- PortalPilot core schema.
-- Temporal owns workflow execution state; these tables are the system of record for everything a human,
-- an auditor or the UI needs to see: what was asked, what the agent did, what it saw, and who intervened.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------- tenancy
CREATE TABLE tenants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text UNIQUE NOT NULL,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- credential vault
-- Envelope encryption: the secret is encrypted with a per-row data key (DEK); the DEK is encrypted with a
-- master key (local Fernet key in dev, AWS KMS in prod). Plaintext never touches this table or the logs.
CREATE TABLE credentials (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenants(id),
    portal          text NOT NULL,                 -- e.g. 'acme-claims'
    label           text NOT NULL,
    username        text NOT NULL,
    secret_ct       bytea NOT NULL,                -- secret encrypted with the DEK
    dek_ct          bytea NOT NULL,                -- DEK encrypted with the master key
    key_provider    text NOT NULL,                 -- 'local' | 'kms'
    key_id          text NOT NULL,                 -- master key id / KMS key ARN
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz,
    revoked_at      timestamptz,
    UNIQUE (tenant_id, portal, label)
);

-- ---------------------------------------------------------------- work queue
CREATE TYPE batch_status AS ENUM ('draft', 'running', 'completed', 'completed_with_errors', 'cancelled');
CREATE TYPE task_status  AS ENUM (
    'invalid',          -- failed onboarding validation, needs data fix before it can run
    'queued', 'running',
    'waiting_human',    -- blocked on an intervention (approval, MFA, takeover)
    'succeeded', 'failed', 'rejected', 'expired', 'cancelled'
);

CREATE TABLE batches (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenants(id),
    name            text NOT NULL,
    portal          text NOT NULL,
    credential_id   uuid REFERENCES credentials(id),
    source_filename text,
    column_mapping  jsonb NOT NULL DEFAULT '{}',   -- source header -> canonical field, with how it was decided
    status          batch_status NOT NULL DEFAULT 'draft',
    approval_threshold numeric(12,2),              -- claims above this amount need a human approval
    max_concurrency int NOT NULL DEFAULT 3,
    workflow_id     text,
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz
);

CREATE TABLE tasks (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES tenants(id),
    batch_id          uuid NOT NULL REFERENCES batches(id),
    row_number        int NOT NULL,
    kind              text NOT NULL,               -- 'submit_claim'
    -- Idempotency key: derived from tenant + portal + business identity of the row, typed into the portal's
    -- reference field so a submission can always be found again on the portal itself.
    idempotency_key   text NOT NULL,
    payload           jsonb NOT NULL,              -- normalized row
    raw_row           jsonb NOT NULL,              -- untouched source row, for lineage
    validation_errors jsonb NOT NULL DEFAULT '[]',
    status            task_status NOT NULL DEFAULT 'queued',
    current_stage     text,
    attempts          int NOT NULL DEFAULT 0,
    workflow_id       text UNIQUE,
    confirmation_no   text,
    error             text,
    cost_usd          numeric(10,5) NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    completed_at      timestamptz,
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX tasks_batch_idx  ON tasks (batch_id, row_number);
CREATE INDEX tasks_status_idx ON tasks (status) WHERE status IN ('running', 'waiting_human');

-- ---------------------------------------------------------------- zero-double-submit ledger
-- One row per task, a small state machine enforced in SQL:
--   (none | reconciled_absent) -> intent -> in_flight -> confirmed
--                                            in_flight -> reconciled_found | reconciled_absent
-- The worker may press submit only after atomically moving intent -> in_flight. If the outcome of the click is
-- unknown (timeout, crash), the row stays in_flight and no further click is possible until a search of the portal
-- (the reconcile) has decided. A second click without a reconcile is impossible even if the workflow has a bug.
CREATE TYPE submit_state AS ENUM ('intent', 'in_flight', 'confirmed', 'reconciled_found', 'reconciled_absent');
CREATE TABLE submit_ledger (
    task_id          uuid PRIMARY KEY REFERENCES tasks(id),
    idempotency_key  text NOT NULL,
    state            submit_state NOT NULL,
    clicks           int NOT NULL DEFAULT 0,       -- how many times the submit button was actually pressed
    confirmation_no  text,
    intent_at        timestamptz NOT NULL DEFAULT now(),
    resolved_at      timestamptz
);

-- ---------------------------------------------------------------- what the agent saw
-- Content-addressed: identical screenshots (common across a batch) are stored once.
CREATE TABLE screenshots (
    sha256       text PRIMARY KEY,
    storage_key  text NOT NULL,
    width        int NOT NULL,
    height       int NOT NULL,
    dom_hash     text,                             -- structural hash of the DOM (tags/roles/labels, no values)
    url          text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- durable step checkpoints
CREATE TYPE step_status AS ENUM ('started', 'succeeded', 'failed', 'skipped');
CREATE TABLE task_steps (
    id              bigserial PRIMARY KEY,
    task_id         uuid NOT NULL REFERENCES tasks(id),
    -- '<stage>/<workflow attempt>/<action index>': an activity retry re-uses the key and upserts,
    -- so retries never create phantom steps.
    step_key        text NOT NULL,
    seq             int NOT NULL,
    stage           text NOT NULL,
    action          text NOT NULL,                 -- click | type | type_secret | select | navigate | read | operator_*
    target          text,                          -- natural-language target description
    value           text,                          -- typed / selected value ('••••••' for secrets)
    status          step_status NOT NULL DEFAULT 'started',
    actor           text NOT NULL DEFAULT 'agent', -- 'agent' | 'operator:<name>'
    grounder        text,                          -- winning grounder: cache | qwen | claude | dom | operator
    grounder_trace  jsonb NOT NULL DEFAULT '[]',   -- every tier tried: point, verified?, latency, cost, reason
    point_x         int,
    point_y         int,
    bbox            jsonb,                         -- {x,y,w,h} of the element under the click, from the verifier
    verified        boolean,
    latency_ms      int,
    cost_usd        numeric(10,6) NOT NULL DEFAULT 0,
    screenshot_before text REFERENCES screenshots(sha256),
    screenshot_after  text REFERENCES screenshots(sha256),
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    UNIQUE (task_id, step_key)
);
CREATE INDEX task_steps_task_idx ON task_steps (task_id, seq);

-- ---------------------------------------------------------------- grounding cache
-- Keyed on page *structure* + instruction, not pixels, so the login page grounded once serves the whole batch.
-- Every cache hit is still re-verified against the live DOM before the click.
CREATE TABLE grounding_cache (
    dom_hash          text NOT NULL,
    instruction_hash  text NOT NULL,
    instruction       text NOT NULL,
    point_x           int NOT NULL,
    point_y           int NOT NULL,
    source_grounder   text NOT NULL,
    hits              int NOT NULL DEFAULT 0,
    misses            int NOT NULL DEFAULT 0,      -- verification failed on a cached point -> evicted when high
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_hit_at       timestamptz,
    PRIMARY KEY (dom_hash, instruction_hash)
);

-- ---------------------------------------------------------------- human in the loop
CREATE TYPE intervention_kind   AS ENUM ('approval', 'mfa_code', 'takeover', 'grounding_failure', 'unexpected_page');
CREATE TYPE intervention_status AS ENUM ('open', 'resolved', 'expired', 'cancelled');
CREATE TABLE interventions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id        uuid NOT NULL REFERENCES tasks(id),
    kind           intervention_kind NOT NULL,
    status         intervention_status NOT NULL DEFAULT 'open',
    stage          text,
    prompt         text NOT NULL,
    screenshot     text REFERENCES screenshots(sha256),
    context        jsonb NOT NULL DEFAULT '{}',
    requested_at   timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz,
    resolved_at    timestamptz,
    resolved_by    text,
    resolution     jsonb                           -- never stores secrets (MFA codes are redacted)
);
CREATE INDEX interventions_open_idx ON interventions (requested_at) WHERE status = 'open';

-- Values a human types for the agent (MFA codes). Kept out of Temporal history: the signal carries only the
-- intervention id; the worker reads the sealed value once and marks it consumed.
CREATE TABLE intervention_secrets (
    intervention_id uuid PRIMARY KEY REFERENCES interventions(id),
    secret_ct       bytea NOT NULL,
    dek_ct          bytea NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    consumed_at     timestamptz
);

-- ---------------------------------------------------------------- live event stream
-- Workers INSERT here and pg_notify; the API LISTENs and fans out over WebSockets. The table doubles as
-- the replay log for a task.
CREATE TABLE task_events (
    id          bigserial PRIMARY KEY,
    task_id     uuid REFERENCES tasks(id),
    batch_id    uuid REFERENCES batches(id),
    type        text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX task_events_task_idx ON task_events (task_id, id);

-- ---------------------------------------------------------------- tamper-evident audit log
-- Append-only (UPDATE/DELETE are rejected) and hash-chained: each row's hash covers the previous row's
-- hash, so a deleted or edited row breaks the chain.
CREATE TABLE audit_log (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid,
    actor       text NOT NULL,                     -- 'system' | 'worker:<id>' | 'operator:<name>'
    action      text NOT NULL,                     -- e.g. 'credential.decrypted', 'submit.clicked'
    entity_type text NOT NULL,
    entity_id   text NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    prev_hash   text,
    hash        text NOT NULL
);

CREATE FUNCTION audit_log_chain() RETURNS trigger AS $$
DECLARE last text;
BEGIN
    PERFORM pg_advisory_xact_lock(4242);
    SELECT hash INTO last FROM audit_log ORDER BY id DESC LIMIT 1;
    NEW.prev_hash := last;
    NEW.hash := encode(digest(
        coalesce(last, '') || NEW.actor || NEW.action || NEW.entity_type || NEW.entity_id ||
        NEW.details::text || NEW.created_at::text, 'sha256'), 'hex');
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER audit_log_chain BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_chain();

CREATE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$ LANGUAGE plpgsql;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

CREATE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$ LANGUAGE plpgsql;
CREATE TRIGGER tasks_touch BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

INSERT INTO tenants (slug, name) VALUES ('demo', 'Demo Tenant');
