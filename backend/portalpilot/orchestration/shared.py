"""Workflow and activity payload types. Kept free of I/O imports so the workflow sandbox can load them.

Nothing secret ever appears in these types: credentials are referenced by id and decrypted inside the activity, and
MFA codes travel as an intervention id. Temporal's event history is therefore safe to inspect and export.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

TASK_QUEUE = "portalpilot"


@dataclass
class TaskContext:
    task_id: str
    tenant_id: str
    batch_id: str
    portal_url: str
    credential_id: Optional[str]
    idempotency_key: str
    amount: float
    approval_threshold: Optional[float]


@dataclass
class SessionInfo:
    session_id: str
    task_queue: str        # the private queue of the worker that holds the browser
    worker_id: str


@dataclass
class StageRequest:
    task_id: str
    session_id: str
    stage: str
    run_key: str           # '<stage>/r<run>': step keys hang off this, so retries upsert instead of duplicating
    ctx: TaskContext
    mfa_intervention_id: Optional[str] = None


@dataclass
class StageResult:
    outcome: str           # ok | needs_input | already_submitted | submitted
    kind: Optional[str] = None
    prompt: Optional[str] = None
    screenshot: Optional[str] = None
    confirmation_no: Optional[str] = None
    cost_usd: float = 0.0
    note: Optional[str] = None


@dataclass
class OperatorAction:
    kind: str              # click | type | key | navigate | refresh
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None
    operator: str = "operator"


@dataclass
class ApprovalDecision:
    approved: bool
    operator: str
    note: str = ""


@dataclass
class InputProvided:
    intervention_id: str
    operator: str


@dataclass
class Release:
    operator: str
    mode: str = "retry"    # retry: re-run the current stage | skip: operator completed the stage by hand


@dataclass
class InterventionRequest:
    task_id: str
    kind: str
    prompt: str
    stage: Optional[str] = None
    screenshot: Optional[str] = None
    context: dict = field(default_factory=dict)
    expires_in_s: Optional[int] = None


@dataclass
class TaskUpdate:
    task_id: str
    status: Optional[str] = None
    stage: Optional[str] = None
    error: Optional[str] = None
    confirmation_no: Optional[str] = None
    attempts_inc: int = 0
    final: bool = False


@dataclass
class LedgerOp:
    task_id: str
    idempotency_key: str
    op: str                # intent | click | confirmed | reconciled_found | reconciled_absent
    confirmation_no: Optional[str] = None


@dataclass
class BatchInput:
    batch_id: str
    task_ids: list[str]
    max_concurrency: int = 3
