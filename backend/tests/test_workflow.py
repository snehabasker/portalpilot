"""ClaimTaskWorkflow against scripted fake activities, in Temporal's time-skipping test server.

The fakes mirror the real contracts, including the submit ledger's state machine (an arm without an intent is a
LedgerViolation), so "zero double-submits" is asserted, not assumed: the fake portal counts every claim created.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from portalpilot.orchestration.shared import (ApprovalDecision, InputProvided, InterventionRequest, LedgerOp,
                                              OperatorAction, Release, SessionInfo, StageRequest, StageResult,
                                              TaskContext, TaskUpdate)
from portalpilot.orchestration.workflows import ClaimTaskWorkflow, TaskWorkflowInput

Q = "test-q"


@dataclass
class Fake:
    """Scripted portal + DB. ``script`` maps stage -> list of behaviours consumed per call."""
    amount: float = 100.0
    threshold: float | None = None
    script: dict = field(default_factory=dict)
    portal_claims: list = field(default_factory=list)   # what the portal actually holds
    ledger_state: str | None = None
    ledger_log: list = field(default_factory=list)
    clicks: int = 0
    calls: list = field(default_factory=list)
    sessions: int = 0
    interventions: dict = field(default_factory=dict)
    statuses: list = field(default_factory=list)
    live_sessions: set = field(default_factory=set)

    def next(self, stage):
        lst = self.script.get(stage)
        return lst.pop(0) if lst else "ok"

    # ---------------------------------------------------------------- db activities
    def acts(self):
        f = self

        @activity.defn(name="load_task")
        async def load_task(task_id: str) -> TaskContext:
            return TaskContext(task_id, "t", "b", "http://portal", "cred", "PP-KEY", f.amount, f.threshold)

        @activity.defn(name="update_task")
        async def update_task(u: TaskUpdate) -> None:
            if u.status:
                f.statuses.append(u.status)

        @activity.defn(name="ledger")
        async def ledger(op: LedgerOp) -> str:
            s = f.ledger_state
            allowed = {"intent": (None, "reconciled_absent", "intent"), "confirmed": ("in_flight",),
                       "reconciled_found": (None, "intent", "in_flight"), "reconciled_absent": (None, "intent", "in_flight")}
            if s not in allowed[op.op]:
                raise ApplicationError(f"illegal {s}->{op.op}", type="LedgerViolation", non_retryable=True)
            f.ledger_state = op.op
            f.ledger_log.append(op.op)
            return op.op

        @activity.defn(name="ledger_state")
        async def ledger_state(task_id: str):
            return f.ledger_state

        @activity.defn(name="open_intervention")
        async def open_intervention(req: InterventionRequest) -> str:
            iid = str(uuid.uuid4())
            f.interventions[iid] = req.kind
            return iid

        @activity.defn(name="close_intervention")
        async def close_intervention(iid: str, status: str, by: str, resolution: dict) -> None:
            f.interventions[iid] = f"{f.interventions[iid]}:{status}"

        # ---------------------------------------------------------------- browser activities
        @activity.defn(name="open_session")
        async def open_session(task_id: str) -> SessionInfo:
            f.sessions += 1
            sid = f"s{f.sessions}"
            f.live_sessions.add(sid)
            return SessionInfo(sid, Q, "w1")

        @activity.defn(name="close_session")
        async def close_session(sid: str) -> None:
            f.live_sessions.discard(sid)

        @activity.defn(name="run_stage")
        async def run_stage(req: StageRequest) -> StageResult:
            f.calls.append(req.stage)
            if req.session_id not in f.live_sessions:
                raise ApplicationError("gone", type="SessionLost", non_retryable=True)
            b = f.next(req.stage)
            if b == "lose_session":
                f.live_sessions.discard(req.session_id)
                raise ApplicationError("gone", type="SessionLost", non_retryable=True)
            if b == "grounding_failed":
                raise ApplicationError("no tier could ground", type="GroundingFailed", non_retryable=True)
            if b == "invalid":
                raise ApplicationError("Policy not found", type="InvalidData", non_retryable=True)
            if req.stage == "login" and b == "mfa" and not req.mfa_intervention_id:
                return StageResult("needs_input", kind="mfa_code", prompt="code?")
            if req.stage == "dedupe_check":
                hit = next((c for c in f.portal_claims if c == "PP-KEY"), None)
                return StageResult("already_submitted", confirmation_no="CLM-000001") if hit else StageResult("ok")
            if req.stage == "submit":
                if f.ledger_state != "intent":   # mirrors _arm_submit
                    raise ApplicationError("not armed", type="LedgerViolation", non_retryable=True)
                f.ledger_state = "in_flight"
                f.clicks += 1
                if b == "commit_then_timeout":      # the portal saved it, the client never heard back
                    f.portal_claims.append("PP-KEY")
                    raise ApplicationError("no confirmation", type="SubmitOutcomeUnknown", non_retryable=True)
                if b == "lost_before_commit":
                    raise ApplicationError("no confirmation", type="SubmitOutcomeUnknown", non_retryable=True)
                f.portal_claims.append("PP-KEY")
                return StageResult("submitted", confirmation_no=f"CLM-{len(f.portal_claims):06d}")
            return StageResult("ok", screenshot="sha")

        @activity.defn(name="operator_action")
        async def operator_action(task_id: str, sid: str, a: OperatorAction):
            f.calls.append(f"op:{a.kind}")
            return "sha-live"

        return [load_task, update_task, ledger, ledger_state, open_intervention, close_intervention, open_session,
                close_session, run_stage, operator_action]


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


async def run(env, fake: Fake, inp: TaskWorkflowInput | None = None, during=None):
    async with Worker(env.client, task_queue=Q, workflows=[ClaimTaskWorkflow], activities=fake.acts()):
        h = await env.client.start_workflow(ClaimTaskWorkflow.run, inp or TaskWorkflowInput("task-1"),
                                            id=f"wf-{uuid.uuid4()}", task_queue=Q)
        if during:
            await during(h)
        return await h.result()


async def wait_for(h, pred, tries=200):
    for _ in range(tries):
        st = await h.query(ClaimTaskWorkflow.state)
        if pred(st):
            return st
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition never met; last state {st}")


async def test_happy_path(env):
    f = Fake()
    r = await run(env, f)
    assert r["status"] == "succeeded" and r["confirmation_no"] == "CLM-000001"
    assert f.calls == ["login", "dedupe_check", "fill_policy", "fill_claimant", "fill_incident", "review", "submit"]
    assert f.ledger_log == ["intent", "confirmed"] and f.clicks == 1 and len(f.portal_claims) == 1


async def test_unknown_outcome_after_commit_reconciles_without_second_click(env):
    f = Fake(script={"submit": ["commit_then_timeout"]})
    r = await run(env, f)
    assert r["status"] == "succeeded"
    assert f.clicks == 1 and len(f.portal_claims) == 1                    # no double submit
    assert f.ledger_log == ["intent", "reconciled_found"]
    assert f.calls[-2:] == ["submit", "dedupe_check"]


async def test_unknown_outcome_before_commit_resubmits_once(env):
    f = Fake(script={"submit": ["lost_before_commit"]})
    r = await run(env, f)
    assert r["status"] == "succeeded"
    assert f.clicks == 2 and len(f.portal_claims) == 1                    # second click only after a clean reconcile
    assert f.ledger_log == ["intent", "reconciled_absent", "intent", "confirmed"]


async def test_session_lost_restarts_from_login_on_new_session(env):
    f = Fake(script={"fill_incident": ["lose_session"]})
    r = await run(env, f)
    assert r["status"] == "succeeded" and f.sessions == 2
    i = f.calls.index("fill_incident")
    assert f.calls[i + 1:i + 3] == ["login", "dedupe_check"]


async def test_already_submitted_by_earlier_run_is_not_resubmitted(env):
    f = Fake(portal_claims=["PP-KEY"])
    r = await run(env, f)
    assert r["status"] == "succeeded" and r["confirmation_no"] == "CLM-000001"
    assert "submit" not in f.calls and f.clicks == 0 and f.ledger_log == ["reconciled_found"]


async def test_approval_granted(env):
    f = Fake(amount=9000, threshold=5000)

    async def approve(h):
        await wait_for(h, lambda s: s["waiting_for"] == "approval")
        await h.signal(ClaimTaskWorkflow.approve, ApprovalDecision(True, "alice"))

    r = await run(env, f, during=approve)
    assert r["status"] == "succeeded"
    assert "approval:resolved" in f.interventions.values()


async def test_approval_rejected(env):
    f = Fake(amount=9000, threshold=5000)

    async def reject(h):
        await wait_for(h, lambda s: s["waiting_for"] == "approval")
        await h.signal(ClaimTaskWorkflow.approve, ApprovalDecision(False, "alice", "duplicate of a phone claim"))

    r = await run(env, f, during=reject)
    assert r["status"] == "rejected" and "submit" not in f.calls and f.clicks == 0


async def test_approval_expires(env):
    f = Fake(amount=9000, threshold=5000)
    r = await run(env, f, TaskWorkflowInput("task-1", approval_timeout_s=3600))  # time-skipping jumps the hour
    assert r["status"] == "expired" and "approval:expired" in f.interventions.values()


async def test_mfa_code_round_trip(env):
    f = Fake(script={"login": ["mfa", "mfa"]})

    async def give_code(h):
        await wait_for(h, lambda s: s["waiting_for"] == "mfa_code")
        iid = next(k for k, v in f.interventions.items() if v == "mfa_code")
        await h.signal(ClaimTaskWorkflow.input_provided, InputProvided(iid, "bob"))

    r = await run(env, f, during=give_code)
    assert r["status"] == "succeeded" and f.calls[:2] == ["login", "login"]


async def test_grounding_failure_hands_control_to_operator_then_reconciles(env):
    f = Fake(script={"fill_claimant": ["grounding_failed"]})

    async def operate(h):
        await wait_for(h, lambda s: s["takeover"] and s["waiting_for"] == "grounding_failure")
        await h.signal(ClaimTaskWorkflow.operator_action, OperatorAction("click", 10, 20, operator="carol"))
        await h.signal(ClaimTaskWorkflow.operator_action, OperatorAction("type", text="Jane Doe", operator="carol"))
        await h.signal(ClaimTaskWorkflow.release, Release("carol", "skip"))

    r = await run(env, f, during=operate)
    assert r["status"] == "succeeded"
    assert "op:click" in f.calls and "op:type" in f.calls
    # 'skip' moved straight on to the next stage...
    after = f.calls[f.calls.index("op:type"):]
    assert after[1] == "fill_incident"
    # ...and because a human had the browser, the portal was searched before the agent was allowed to submit.
    # (The reconcile leaves the form, so the agent refills it: correctness over speed, by design.)
    assert after.index("dedupe_check") < after.index("submit")
    assert f.clicks == 1 and len(f.portal_claims) == 1


async def test_operator_takeover_on_request(env):
    f = Fake(amount=9000, threshold=5000)

    async def grab(h):
        await wait_for(h, lambda s: s["waiting_for"] == "approval")
        await h.signal(ClaimTaskWorkflow.request_takeover, "dave")
        await wait_for(h, lambda s: s["takeover"])
        await h.signal(ClaimTaskWorkflow.release, Release("dave", "retry"))
        await wait_for(h, lambda s: not s["takeover"] and s["waiting_for"] == "approval")
        await h.signal(ClaimTaskWorkflow.approve, ApprovalDecision(True, "dave"))

    r = await run(env, f, during=grab)
    assert r["status"] == "succeeded" and f.clicks == 1
    i = f.calls.index("review")
    assert "dedupe_check" in f.calls[i:]   # takeover during approval forces a reconcile before submit


async def test_invalid_data_fails_without_human(env):
    f = Fake(script={"fill_policy": ["invalid"]})
    r = await run(env, f)
    assert r["status"] == "failed" and "InvalidData" in r["error"]
