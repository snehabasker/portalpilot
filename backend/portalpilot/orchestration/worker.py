"""Worker process: one shared-queue worker (workflows, DB activities, open_session) and one private-queue worker
(browser activities for the sessions this process holds)."""
from __future__ import annotations

import asyncio
import logging
import signal

from prometheus_client import start_http_server
from temporalio.client import Client
from temporalio.worker import Worker

from .. import db, storage
from ..browser.session import SessionManager
from ..config import settings
from ..grounding import GroundingRouter
from .activities import BrowserActivities, DbActivities
from .workflows import BatchWorkflow, ClaimTaskWorkflow

log = logging.getLogger("portalpilot.worker")


async def connect_temporal(retries: int = 60) -> Client:
    s = settings()
    for i in range(retries):
        try:
            return await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
        except Exception as e:  # Temporal may still be starting
            log.info("temporal not ready (%s), retry %d", e, i)
            await asyncio.sleep(2)
    raise RuntimeError("could not connect to Temporal")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = settings()
    start_http_server(s.metrics_port)  # Prometheus scrape target for this worker's counters
    await db.migrate()
    await asyncio.to_thread(storage.ensure_bucket)
    client = await connect_temporal()

    sessions = SessionManager()
    await sessions.start()
    private_queue = f"{s.task_queue}-browser-{s.worker_id}"
    router = GroundingRouter()
    dba = DbActivities()
    ba = BrowserActivities(sessions, router, private_queue)
    log.info("worker %s: grounder chain %s, private queue %s", s.worker_id, ",".join(s.grounder_chain), private_queue)

    shared = Worker(client, task_queue=s.task_queue, workflows=[ClaimTaskWorkflow, BatchWorkflow],
                    activities=[dba.load_task, dba.update_task, dba.ledger, dba.ledger_state, dba.open_intervention,
                                dba.close_intervention, dba.finalize_batch, ba.open_session],
                    max_concurrent_activities=20)
    private = Worker(client, task_queue=private_queue,
                     activities=[ba.run_stage, ba.operator_action, ba.close_session],
                     max_concurrent_activities=s.max_browser_sessions)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows dev runs
            pass
    reaper = asyncio.create_task(sessions.reaper())
    async with shared, private:
        await stop.wait()
    reaper.cancel()
    await sessions.stop()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
