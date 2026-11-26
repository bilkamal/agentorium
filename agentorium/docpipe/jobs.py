"""In-process job runner backed by SQLite: tasks run in parallel (bounded), survive restarts,
pause when the LLM usage limit is hit and resume later without redoing finished tasks."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from ..config import settings
from ..llm import LLMRateLimited
from .models import Job, Task
from .store import get_store

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 2
ACTIVE = ("queued", "running", "paused", "finalizing")

Handler = Callable[[Job, Task], Awaitable[dict]]
Finalizer = Callable[[Job], Awaitable[None]]
Listener = Callable[[Job, str, dict], None]


class JobRunner:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._finalizers: dict[str, Finalizer] = {}
        self._preparers: dict[str, tuple[Finalizer, bool]] = {}
        self._listeners: list[Listener] = []
        self._running: dict[str, asyncio.Task] = {}

    # ---------------------------------------------------------- registration
    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def register_finalizer(self, job_type: str, fn: Finalizer) -> None:
        self._finalizers[job_type] = fn

    def register_preparer(self, job_type: str, fn: Finalizer, required: bool = False) -> None:
        """Runs once when the job starts or resumes, before any task. A failing `required` preparer fails the job;
        an optional one is only logged."""
        self._preparers[job_type] = (fn, required)

    def on_event(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def emit(self, job: Job, event: str, payload: dict | None = None) -> None:
        for fn in self._listeners:
            try:
                fn(job, event, payload or {})
            except Exception:  # listeners are best effort
                log.exception("job listener failed")

    # -------------------------------------------------------------- control
    def is_running(self, job_id: str) -> bool:
        t = self._running.get(job_id)
        return t is not None and not t.done()

    async def start(self, job_id: str) -> None:
        if self.is_running(job_id):
            return
        task = asyncio.get_running_loop().create_task(self._run(job_id))
        self._running[job_id] = task
        task.add_done_callback(lambda _t, j=job_id: self._running.pop(j, None) if self._running.get(j) is _t else None)

    async def wait(self, job_id: str) -> None:
        t = self._running.get(job_id)
        if t is not None:
            await asyncio.gather(t, return_exceptions=True)

    async def cancel(self, job_id: str) -> bool:
        store = get_store()
        job = store.get_job(job_id)
        if job is None or job.status not in ACTIVE:
            return False
        store.update_job(job_id, status="cancelled", finished_at=time.time())
        t = self._running.get(job_id)
        if t is not None:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        store.requeue_running_tasks(job_id)
        self.emit(store.get_job(job_id), "cancelled")
        return True

    # ------------------------------------------------------------ execution
    async def _run(self, job_id: str) -> None:
        store = get_store()
        job = store.get_job(job_id)
        if job is None or job.status not in ACTIVE:
            return
        store.requeue_running_tasks(job_id)
        job = store.update_job(job_id, status="running", started_at=job.started_at or time.time(), resume_at=None)
        self.emit(job, "started")
        try:
            prep, required = self._preparers.get(job.type, (None, False))
            while prep is not None:
                try:
                    await prep(store.get_job(job_id))
                except asyncio.CancelledError:
                    raise
                except LLMRateLimited as exc:
                    await self._pause(job_id, exc)
                    continue
                except Exception as exc:  # e.g. indexing failed — sections can still be reviewed
                    log.warning("preparer failed for job %s: %s", job_id, exc)
                    if required:
                        job = store.update_job(job_id, status="failed", error=f"persiapan gagal: {exc}"[:1000],
                                               finished_at=time.time())
                        self.emit(job, "finished", {"status": "failed"})
                        return
                break
            while True:
                limited: list[LLMRateLimited] = []
                queued = [t for t in store.get_tasks(job_id) if t.status == "queued"]
                if not queued:
                    break
                sem = asyncio.Semaphore(max(1, settings.llm_concurrency))
                await asyncio.gather(*(self._run_task(job_id, t, sem, limited) for t in queued))
                if limited:
                    await self._pause(job_id, limited[0])
            await self._finish(job_id)
        except asyncio.CancelledError:
            store.requeue_running_tasks(job_id)
            raise

    async def _run_task(self, job_id: str, task: Task, sem: asyncio.Semaphore, limited: list) -> None:
        store = get_store()
        async with sem:
            if limited:  # usage limit hit by a sibling: leave queued for after the pause
                return
            job = store.get_job(job_id)
            handler = self._handlers.get(task.kind)
            store.update_task(task.id, status="running")
            task = store.get_task(task.id)
            self.emit(job, "task_started", {"task_id": task.id, "section_id": task.section_id})
            try:
                if handler is None:
                    raise RuntimeError(f"no handler for task kind {task.kind!r}")
                output = await handler(job, task)
            except asyncio.CancelledError:
                store.update_task(task.id, status="queued")
                raise
            except LLMRateLimited as exc:
                store.update_task(task.id, status="queued")
                limited.append(exc)
                return
            except Exception as exc:
                attempts = task.attempts + 1
                log.warning("task %s failed (attempt %d): %s", task.id, attempts, exc)
                if attempts < MAX_ATTEMPTS and handler is not None:
                    store.update_task(task.id, status="queued", attempts=attempts, error=str(exc)[:1000])
                else:
                    store.update_task(task.id, status="failed", attempts=attempts, error=str(exc)[:1000])
                    store.increment_job(job_id, "failed_tasks")
                    self.emit(store.get_job(job_id), "task_failed",
                              {"task_id": task.id, "section_id": task.section_id, "error": str(exc)[:300]})
                return
            store.update_task(task.id, status="done", attempts=task.attempts + 1, error=None, output=output or {})
            store.increment_job(job_id, "done_tasks")
            self.emit(store.get_job(job_id), "task_done",
                      {"task_id": task.id, "section_id": task.section_id, "output": output or {}})

    async def _pause(self, job_id: str, exc: LLMRateLimited) -> None:
        store = get_store()
        wait = float(settings.rate_limit_wait)
        if exc.reset_at and exc.reset_at > time.time():
            wait = max(wait, exc.reset_at - time.time())
        job = store.update_job(job_id, status="paused", resume_at=time.time() + wait)
        self.emit(job, "paused", {"resume_at": job.resume_at, "reason": str(exc)[:200]})
        await asyncio.sleep(wait)
        job = store.update_job(job_id, status="running", resume_at=None)
        self.emit(job, "resumed")

    async def _finish(self, job_id: str) -> None:
        store = get_store()
        job = store.update_job(job_id, status="finalizing")
        error = None
        fin = self._finalizers.get(job.type)
        if fin is not None:
            self.emit(job, "finalizing")
            while True:
                try:
                    await fin(store.get_job(job_id))
                except asyncio.CancelledError:
                    raise
                except LLMRateLimited as exc:  # quota ran out in the final step: wait, then redo it
                    await self._pause(job_id, exc)
                    store.update_job(job_id, status="finalizing")
                    continue
                except Exception as exc:
                    log.exception("finalizer failed for job %s", job_id)
                    error = f"finalizer: {exc}"[:1000]
                break
        job = store.get_job(job_id)
        status = "partial" if (job.failed_tasks or error) else "done"
        job = store.update_job(job_id, status=status, error=error, finished_at=time.time())
        self.emit(job, "finished", {"status": status})


runner = JobRunner()


async def resume_jobs() -> list[str]:
    """Restart jobs that were active when the server stopped. Returns the restarted job ids."""
    store = get_store()
    started = []
    for job in store.list_jobs(statuses=ACTIVE):
        store.requeue_running_tasks(job.id)
        await runner.start(job.id)
        started.append(job.id)
    return started
