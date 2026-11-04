"""Durable workflows.

ClaimTaskWorkflow drives one claim through the playbook stages. It owns three guarantees:

1. Durability: every stage result is in Temporal history; a worker crash resumes at the last finished stage
   on a fresh browser session (re-login, then the dedupe check).
2. Zero double-submits: submit is a one-shot activity guarded by the submit ledger. If its outcome is unknown,
   the workflow reconciles by searching the portal for the idempotency key before any further click.
3. Human control: approvals, MFA codes and operator takeovers are signals. Waiting for a human costs nothing
   (no worker, no browser is blocked), and every wait has a deadline.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (ActivityError, ApplicationError, ChildWorkflowError, TimeoutError as TTimeout,
                                   TimeoutType)

with workflow.unsafe.imports_passed_through():
    from .activities import BrowserActivities, DbActivities
    from .shared import (ApprovalDecision, BatchInput, InputProvided, InterventionRequest, LedgerOp, OperatorAction,
                         Release, SessionInfo, StageRequest, StageResult, TaskContext, TaskUpdate)

STAGES = ["login", "dedupe_check", "fill_policy", "fill_claimant", "fill_incident", "review", "submit"]

DB_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30), maximum_attempts=10)
STAGE_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0,
                          maximum_interval=timedelta(seconds=30), maximum_attempts=3,
                          non_retryable_error_types=["SessionLost", "BadCredentials", "InvalidData",
                                                     "SubmitOutcomeUnknown", "GroundingFailed", "LedgerViolation"])
ONE_SHOT = RetryPolicy(maximum_attempts=1)


@dataclass
class TaskWorkflowInput:
    task_id: str
    approval_timeout_s: int = 72 * 3600
    mfa_timeout_s: int = 24 * 3600
    takeover_timeout_s: int = 24 * 3600
    max_submit_attempts: int = 3
    max_session_reopens: int = 3


class _SessionGone(Exception):
    pass


class _TaskEnded(Exception):
    def __init__(self, status: str, error: str | None = None):
        self.status, self.error = status, error


def _error_type(e: ActivityError) -> str | None:
    c = e.cause
    if isinstance(c, ApplicationError):
        return c.type
    if isinstance(c, TTimeout):
        return f"Timeout.{TimeoutType(c.type).name}" if c.type is not None else "Timeout"
    return None


@workflow.defn
class ClaimTaskWorkflow:
    def __init__(self) -> None:
        self.status = "queued"
        self.stage: Optional[str] = None
        self.session: Optional[SessionInfo] = None
        self.approval: Optional[ApprovalDecision] = None
        self.provided: list[InputProvided] = []
        self.takeover = False
        self.takeover_by: Optional[str] = None
        self.release_req: Optional[Release] = None
        self.ops: list[OperatorAction] = []
        self.last_screenshot: Optional[str] = None
        self.waiting_for: Optional[str] = None
        self.cost_usd = 0.0
        self.needs_reconcile = False
        self.history: list[dict] = []

    # ------------------------------------------------------------------------------------------ signals / queries
    @workflow.signal
    def approve(self, d: ApprovalDecision) -> None:
        self.approval = d

    @workflow.signal
    def input_provided(self, i: InputProvided) -> None:
        self.provided.append(i)

    @workflow.signal
    def request_takeover(self, operator: str) -> None:
        if not self.takeover:
            self.takeover, self.takeover_by = True, operator

    @workflow.signal
    def release(self, r: Release) -> None:
        if self.takeover:
            self.release_req = r

    @workflow.signal
    def operator_action(self, a: OperatorAction) -> None:
        if self.takeover:
            self.ops.append(a)

    @workflow.query
    def state(self) -> dict:
        return {"status": self.status, "stage": self.stage, "takeover": self.takeover, "takeover_by": self.takeover_by,
                "waiting_for": self.waiting_for, "last_screenshot": self.last_screenshot,
                "session_worker": self.session.worker_id if self.session else None,
                "cost_usd": round(self.cost_usd, 5), "history": self.history[-50:]}

    # ------------------------------------------------------------------------------------------ main
    @workflow.run
    async def run(self, inp: TaskWorkflowInput) -> dict:
        self.inp = inp
        ctx: TaskContext = await self._db(DbActivities.load_task, inp.task_id)
        self.ctx = ctx
        await self._update(status="running", attempts_inc=1)
        self.status = "running"
        confirmation: Optional[str] = None
        try:
            confirmation = await self._drive(ctx)
            self.status = "succeeded"
            await self._update(status="succeeded", confirmation_no=confirmation, final=True)
            return {"status": "succeeded", "confirmation_no": confirmation, "cost_usd": self.cost_usd}
        except _TaskEnded as end:
            self.status = end.status
            await self._update(status=end.status, error=end.error, final=True)
            return {"status": end.status, "error": end.error, "cost_usd": self.cost_usd}
        finally:
            if self.session:
                try:
                    await workflow.execute_activity_method(
                        BrowserActivities.close_session, self.session.session_id, task_queue=self.session.task_queue,
                        schedule_to_start_timeout=timedelta(seconds=20), start_to_close_timeout=timedelta(seconds=20),
                        retry_policy=ONE_SHOT)
                except ActivityError:
                    pass  # the worker is gone; its sessions went with it

    async def _drive(self, ctx: TaskContext) -> str:
        idx = 0
        runs: dict[str, int] = {}
        reopens = 0
        submit_attempts = 0
        approved = False
        mfa_iid: Optional[str] = None

        await self._open_session()
        while idx < len(STAGES):
            stage = STAGES[idx]
            self.stage = stage

            if self.takeover:                                   # operator grabbed control between stages
                mode = await self._takeover_loop(stage, "takeover", f"{self.takeover_by} took control")
                if mode == "skip":
                    idx += 1
                    continue

            if stage == "submit":
                if self.needs_reconcile:              # a human had the browser: they may have submitted by hand
                    idx = STAGES.index("dedupe_check")
                    continue
                submit_attempts += 1
                if submit_attempts > self.inp.max_submit_attempts:
                    raise _TaskEnded("failed", f"submit did not succeed after {self.inp.max_submit_attempts} attempts")
                await self._db(DbActivities.ledger, LedgerOp(ctx.task_id, ctx.idempotency_key, "intent"))

            runs[stage] = runs.get(stage, 0) + 1
            await self._update(stage=stage)
            req = StageRequest(ctx.task_id, self.session.session_id, stage, f"{stage}/r{runs[stage]}", ctx,
                               mfa_intervention_id=mfa_iid if stage == "login" else None)
            try:
                res: StageResult = await self._browser(BrowserActivities.run_stage, req,
                                                       retry=ONE_SHOT if stage == "submit" else STAGE_RETRY)
            except _SessionGone:
                reopens += 1
                self._log(stage, "session_lost")
                if reopens > self.inp.max_session_reopens:
                    raise _TaskEnded("failed", "browser session lost too many times")
                await self._open_session()
                idx = 0                                         # login -> dedupe_check reconciles anything in flight
                continue
            except ActivityError as e:
                typ = _error_type(e)
                msg = str(e.cause or e)
                self._log(stage, f"error:{typ}", msg)
                if stage == "submit" and typ in ("SubmitOutcomeUnknown", "Timeout.START_TO_CLOSE", None):
                    idx = STAGES.index("dedupe_check")          # outcome unknown -> reconcile against the portal
                    continue
                if typ in ("InvalidData", "BadCredentials", "LedgerViolation"):
                    raise _TaskEnded("failed", f"{typ}: {msg}")
                # perception or navigation trouble the retries could not fix: hand the wheel to a human
                kind = "grounding_failure" if typ == "GroundingFailed" else "unexpected_page"
                mode = await self._takeover_loop(stage, kind, f"Agent is stuck at '{stage}': {msg}", auto=True)
                idx += 1 if mode == "skip" else 0
                continue

            self.cost_usd += res.cost_usd
            if res.screenshot:
                self.last_screenshot = res.screenshot
            self._log(stage, res.outcome, res.note)

            if res.outcome == "needs_input":                    # MFA
                mfa_iid = await self._wait_for_input(stage, res)
                continue                                        # re-run login with the code
            if stage == "login":
                mfa_iid = None

            if stage == "dedupe_check":
                self.needs_reconcile = False
                state = await self._db(DbActivities.ledger_state, ctx.task_id)
                if res.outcome == "already_submitted":
                    await self._db(DbActivities.ledger, LedgerOp(ctx.task_id, ctx.idempotency_key, "reconciled_found",
                                                                 res.confirmation_no))
                    return res.confirmation_no
                if state == "in_flight":
                    await self._db(DbActivities.ledger, LedgerOp(ctx.task_id, ctx.idempotency_key, "reconciled_absent"))

            if stage == "review" and not approved and ctx.approval_threshold is not None \
                    and ctx.amount > ctx.approval_threshold:
                await self._wait_for_approval(res)
                approved = True

            if stage == "submit" and res.outcome == "submitted":
                await self._db(DbActivities.ledger, LedgerOp(ctx.task_id, ctx.idempotency_key, "confirmed",
                                                             res.confirmation_no))
                return res.confirmation_no
            idx += 1
        raise _TaskEnded("failed", "playbook ended without a confirmation")

    # ------------------------------------------------------------------------------------------ humans
    async def _wait_human(self, pred, timeout_s: int) -> bool:
        """Wait for ``pred`` while still serving takeovers. False on deadline."""
        deadline = workflow.now() + timedelta(seconds=timeout_s)
        while not pred():
            remaining = (deadline - workflow.now()).total_seconds()
            if remaining <= 0:
                return False
            try:
                await workflow.wait_condition(lambda: pred() or self.takeover, timeout=timedelta(seconds=remaining))
            except asyncio.TimeoutError:
                return pred()
            if not pred() and self.takeover:
                await self._takeover_loop(self.stage or "", "takeover", f"{self.takeover_by} took control")
        return True

    async def _wait_for_approval(self, res: StageResult) -> None:
        ctx = self.ctx
        iid = await self._db(DbActivities.open_intervention, InterventionRequest(
            ctx.task_id, "approval", f"Approve submitting a claim of {ctx.amount:,.2f} "
            f"(above the {ctx.approval_threshold:,.2f} threshold)?", "review", res.screenshot,
            {"amount": ctx.amount, "threshold": ctx.approval_threshold}, self.inp.approval_timeout_s))
        self.status, self.waiting_for = "waiting_human", "approval"
        self.approval = None
        got = await self._wait_human(lambda: self.approval is not None, self.inp.approval_timeout_s)
        self.status, self.waiting_for = "running", None
        if not got:
            await self._close_iv(iid, "expired", "system", {})
            raise _TaskEnded("expired", "approval not given before the deadline")
        d = self.approval
        await self._close_iv(iid, "resolved", d.operator, {"approved": d.approved, "note": d.note})
        if not d.approved:
            raise _TaskEnded("rejected", f"rejected by {d.operator}: {d.note}")

    async def _wait_for_input(self, stage: str, res: StageResult) -> str:
        iid = await self._db(DbActivities.open_intervention, InterventionRequest(
            self.ctx.task_id, res.kind or "mfa_code", res.prompt or "Input needed", stage, res.screenshot, {},
            self.inp.mfa_timeout_s))
        self.status, self.waiting_for = "waiting_human", res.kind
        got = await self._wait_human(lambda: any(p.intervention_id == iid for p in self.provided), self.inp.mfa_timeout_s)
        self.status, self.waiting_for = "running", None
        if not got:
            await self._close_iv(iid, "expired", "system", {})
            raise _TaskEnded("expired", f"no {res.kind} provided before the deadline")
        p = next(p for p in self.provided if p.intervention_id == iid)
        await self._close_iv(iid, "resolved", p.operator, {"provided": True})
        return iid

    async def _takeover_loop(self, stage: str, kind: str, prompt: str, auto: bool = False) -> str:
        """Operator drives the agent's browser through signals. Returns the release mode (retry | skip)."""
        prev_status, prev_waiting = self.status, self.waiting_for   # a takeover can interrupt another wait
        self.takeover = True
        if auto:
            self.takeover_by = "agent-request"
        self.release_req = None
        iid = await self._db(DbActivities.open_intervention, InterventionRequest(
            self.ctx.task_id, kind, prompt, stage, self.last_screenshot, {"takeover_by": self.takeover_by},
            self.inp.takeover_timeout_s))
        self.status, self.waiting_for = "waiting_human", kind
        await self._operator_refresh()
        while True:
            try:
                await workflow.wait_condition(lambda: bool(self.ops) or self.release_req is not None,
                                              timeout=timedelta(seconds=self.inp.takeover_timeout_s))
            except asyncio.TimeoutError:
                await self._close_iv(iid, "expired", "system", {})
                raise _TaskEnded("expired", "operator takeover timed out")
            while self.ops:
                a = self.ops.pop(0)
                try:
                    sha = await self._browser(BrowserActivities.operator_action, self.ctx.task_id,
                                              self.session.session_id, a, retry=ONE_SHOT)
                    self.last_screenshot = sha or self.last_screenshot
                except _SessionGone:
                    await self._open_session()
                except ActivityError as e:
                    self._log(stage, "operator_action_failed", str(e.cause or e))
            if self.release_req is not None:
                break
        r = self.release_req
        await self._close_iv(iid, "resolved", r.operator, {"mode": r.mode})
        self._log(stage, f"released:{r.mode}", r.operator)
        self.takeover, self.takeover_by, self.release_req = False, None, None
        self.needs_reconcile = True
        self.status, self.waiting_for = (prev_status, prev_waiting) if prev_waiting else ("running", None)
        return r.mode

    async def _operator_refresh(self) -> None:
        try:
            sha = await self._browser(BrowserActivities.operator_action, self.ctx.task_id, self.session.session_id,
                                      OperatorAction("refresh"), retry=ONE_SHOT)
            self.last_screenshot = sha or self.last_screenshot
        except (_SessionGone, ActivityError):
            pass

    # ------------------------------------------------------------------------------------------ plumbing
    async def _open_session(self) -> None:
        self.session = await workflow.execute_activity_method(
            BrowserActivities.open_session, self.ctx.task_id, start_to_close_timeout=timedelta(seconds=60),
            retry_policy=DB_RETRY)
        self._log("session", "opened", self.session.worker_id)

    async def _browser(self, method, *args, retry: RetryPolicy):
        try:
            return await workflow.execute_activity_method(
                method, args=list(args), task_queue=self.session.task_queue,
                schedule_to_start_timeout=timedelta(seconds=30),   # worker holding the browser is gone
                start_to_close_timeout=timedelta(minutes=3),
                heartbeat_timeout=timedelta(seconds=45),
                retry_policy=retry)
        except ActivityError as e:
            typ = _error_type(e)
            if typ in ("SessionLost", "Timeout.SCHEDULE_TO_START", "Timeout.HEARTBEAT"):
                raise _SessionGone() from e
            raise

    async def _db(self, method, arg):
        return await workflow.execute_activity_method(method, arg, start_to_close_timeout=timedelta(seconds=30),
                                                      retry_policy=DB_RETRY)

    async def _update(self, **kw) -> None:
        await self._db(DbActivities.update_task, TaskUpdate(self.ctx.task_id, **kw))

    async def _close_iv(self, iid: str, status: str, by: str, resolution: dict) -> None:
        await workflow.execute_activity_method(DbActivities.close_intervention, args=[iid, status, by, resolution],
                                               start_to_close_timeout=timedelta(seconds=30), retry_policy=DB_RETRY)

    def _log(self, stage: str, event: str, detail: Optional[str] = None) -> None:
        self.history.append({"t": workflow.now().isoformat(), "stage": stage, "event": event,
                             "detail": (detail or "")[:300]})


@workflow.defn
class BatchWorkflow:
    """Fans a batch out to one child workflow per task with bounded concurrency (the portal is fragile and the
    tenant's account may be rate-limited). Child ids are task-<id>, so a replayed batch cannot start a task twice."""

    def __init__(self) -> None:
        self.running = 0
        self.done = 0
        self.results: dict[str, str] = {}

    @workflow.query
    def progress(self) -> dict:
        return {"running": self.running, "done": self.done, "results": self.results}

    @workflow.run
    async def run(self, inp: BatchInput) -> dict:
        async def one(task_id: str) -> None:
            try:
                r = await workflow.execute_child_workflow(ClaimTaskWorkflow.run, TaskWorkflowInput(task_id),
                                                          id=f"task-{task_id}")
                self.results[task_id] = r["status"]
            except ChildWorkflowError as e:
                self.results[task_id] = f"error: {e.cause or e}"
            finally:
                self.running -= 1
                self.done += 1

        children = []
        for tid in inp.task_ids:
            await workflow.wait_condition(lambda: self.running < inp.max_concurrency)
            self.running += 1
            children.append(asyncio.create_task(one(tid)))
        await asyncio.gather(*children)
        status = await workflow.execute_activity_method(DbActivities.finalize_batch, inp.batch_id,
                                                        start_to_close_timeout=timedelta(seconds=30),
                                                        retry_policy=DB_RETRY)
        return {"status": status, "results": self.results}
