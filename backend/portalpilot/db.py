"""asyncpg pool, migrations, and the small set of writes shared by the API and the worker."""
from __future__ import annotations

import json
from importlib import resources
from typing import Any

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    for t in ("json", "jsonb"):
        await conn.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings().database_url, min_size=1, max_size=10, init=_init_conn)
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def migrate() -> list[str]:
    """Apply migrations/NNN_*.sql in order, each once. Guarded by an advisory lock so replicas can race safely."""
    p = await pool()
    applied: list[str] = []
    async with p.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock(7331)")
        try:
            await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
            done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
            files = sorted(f for f in resources.files("portalpilot.migrations").iterdir() if f.name.endswith(".sql"))
            for f in files:
                if f.name in done:
                    continue
                async with conn.transaction():
                    await conn.execute(f.read_text(encoding="utf-8"))
                    await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", f.name)
                applied.append(f.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(7331)")
    return applied


async def audit(actor: str, action: str, entity_type: str, entity_id: Any, details: dict | None = None,
                tenant_id: Any = None, conn: asyncpg.Connection | None = None) -> None:
    sql = ("INSERT INTO audit_log (tenant_id, actor, action, entity_type, entity_id, details) "
           "VALUES ($1, $2, $3, $4, $5, $6)")
    args = (tenant_id, actor, action, entity_type, str(entity_id), details or {})
    if conn is not None:
        await conn.execute(sql, *args)
    else:
        await (await pool()).execute(sql, *args)


async def publish(type_: str, payload: dict, task_id: Any = None, batch_id: Any = None) -> None:
    """Persist an event and wake every API replica's WebSocket fan-out via NOTIFY (payload is just the id,
    because NOTIFY payloads are capped at 8 kB)."""
    p = await pool()
    async with p.acquire() as conn:
        eid = await conn.fetchval(
            "INSERT INTO task_events (task_id, batch_id, type, payload) VALUES ($1, $2, $3, $4) RETURNING id",
            task_id, batch_id, type_, payload)
        await conn.execute("SELECT pg_notify('task_events', $1)", str(eid))
