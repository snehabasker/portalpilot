"""Settings, read once from the environment. Every service (api, worker) uses the same class."""
from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise RuntimeError(f"missing required env var {name}")
    return v


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://portalpilot:portalpilot@localhost:5432/portalpilot"))
    temporal_address: str = field(default_factory=lambda: _env("TEMPORAL_ADDRESS", "localhost:7233"))
    temporal_namespace: str = field(default_factory=lambda: _env("TEMPORAL_NAMESPACE", "default"))
    task_queue: str = field(default_factory=lambda: _env("TASK_QUEUE", "portalpilot"))

    # screenshot store: S3 in AWS, MinIO locally (same API)
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET", "portalpilot-screens"))
    s3_endpoint: str | None = field(default_factory=lambda: os.environ.get("S3_ENDPOINT") or None)
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "us-east-1"))

    # credential vault: 'local' (Fernet master key from env) or 'kms'
    key_provider: str = field(default_factory=lambda: _env("KEY_PROVIDER", "local"))
    local_master_key: str | None = field(default_factory=lambda: os.environ.get("LOCAL_MASTER_KEY") or None)
    kms_key_id: str | None = field(default_factory=lambda: os.environ.get("KMS_KEY_ID") or None)

    # grounding: ordered chain of tiers. 'cache' and 'qwen' are cheap; 'claude' is the expensive fallback;
    # 'dom' is a deterministic grounder for CI and machines without a GPU or API key.
    grounder_chain: tuple[str, ...] = field(default_factory=lambda: tuple(
        t.strip() for t in _env("GROUNDER_CHAIN", "cache,qwen,claude").split(",") if t.strip()))
    qwen_url: str = field(default_factory=lambda: _env("QWEN_URL", "http://host.docker.internal:8000"))
    qwen_model: str = field(default_factory=lambda: _env("QWEN_MODEL", "merged-clean"))
    qwen_timeout_s: float = field(default_factory=lambda: float(_env("QWEN_TIMEOUT_S", "8")))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5-5"))

    operator_token: str = field(default_factory=lambda: _env("OPERATOR_TOKEN", "dev-operator-token"))
    viewport_w: int = 1280
    viewport_h: int = 800
    worker_id: str = field(default_factory=lambda: os.environ.get("WORKER_ID") or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}")
    max_browser_sessions: int = field(default_factory=lambda: int(_env("MAX_BROWSER_SESSIONS", "4")))
    metrics_port: int = field(default_factory=lambda: int(_env("METRICS_PORT", "9100")))

    # outbound domain events (transactional outbox relay): 'none', 'log' or 'eventbridge'
    event_sink: str = field(default_factory=lambda: _env("EVENT_SINK", "none"))
    event_bus_name: str = field(default_factory=lambda: _env("EVENT_BUS_NAME", "portalpilot"))
    event_relay_interval_s: float = field(default_factory=lambda: float(_env("EVENT_RELAY_INTERVAL_S", "2")))


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
