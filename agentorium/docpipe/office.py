"""Show document jobs in the office visualiser: one external session per job, agents from the registry.
Everything here is best effort — a visualiser problem never breaks a review."""
from __future__ import annotations

import functools
import logging
import threading

from ..registry import registry
from .jobs import runner
from .models import Job
from .store import get_store

log = logging.getLogger(__name__)
bridge: "OfficeBridge | None" = None
_listening = False


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            log.warning("office bridge %s failed: %s", fn.__name__, exc)
            return None
    return wrapper


def _describe(x: dict) -> str:
    """One line for a finding or an edit, as the agents exchanged it."""
    if "new_text" in x:
        return f"{x.get('block_id', '')}: “{_short(x.get('new_text', ''), 160)}” — {_short(x.get('reason', ''), 120)}"
    quote = f" — “{_short(x.get('quote', ''), 80)}”" if x.get("quote") else ""
    return f"[{x.get('category', '')}] {_short(x.get('description', ''), 220)}{quote}"


def _why(x: dict) -> str:
    note = x.get("validator_note") or x.get("note") or ""
    return _short(note.removeprefix("supervisor: ").removeprefix("supervisor:"), 200) or "tanpa alasan"


def _short(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _cap(s: str, n: int = 4000) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class OfficeBridge:
    def __init__(self, orchestrator):
        self.orch = orchestrator
        self._lock = threading.Lock()

    # --------------------------------------------------------------- session
    def _session(self, job: Job):
        store = get_store()
        with self._lock:
            sid = (store.get_job(job.id) or job).params.get("session_id")
            s = self.orch.get(sid) if sid else None
            if s is not None:
                return s
            doc = store.get_document(job.document_id)
            version = store.get_version(job.version_id)
            kind = {"review": "Review", "revision": "Revisi", "instructed_revision": "Revisi dari bahan",
                    "update": "Update berkala"}.get(job.type, job.type)
            title = f"{kind}: {doc.title if doc else '?'} v{version.version_no if version else '?'}"
            s = self.orch.create_session(kind="external", title=title)
            fresh = store.get_job(job.id) or job
            store.update_job(job.id, params={**fresh.params, "session_id": s.id})
            team = registry.by_role("supervisor")[:1] + registry.workers()
            for a in team:
                self.orch.push_external(s, {"type": "register_agent", "id": a.id, "name": a.name, "role": a.role,
                                            "color": a.color, "hair": a.hair, "skills": a.skills})
            sections = {sec.id: sec for sec in store.get_sections(job.version_id)}
            workers = registry.workers() or [None]
            steps = []
            for t in store.get_tasks(job.id):
                sec = sections.get(t.section_id)
                w = workers[t.ordinal % len(workers)]
                steps.append({"id": t.ordinal + 1, "title": sec.heading if sec else t.section_id,
                              "assignee": w.id if w else "", "instructions": sec.path if sec else "",
                              "deliverable": "temuan review" if job.type == "review" else "usulan edit"})
            self.orch.push_external(s, {"type": "plan", "status": "agreed",
                                        "plan": {"objective": title, "steps": steps}})
            self.orch.push_external(s, {"type": "stage", "stage": "executing"})
            return s

    def session_for(self, job: Job) -> str | None:
        s = _safe(self._session)(job)
        return s.id if s else None

    def _push(self, job: Job, event: dict) -> None:
        s = self._session(job)
        self.orch.push_external(s, event)

    # ------------------------------------------------------- review hooks
    @_safe
    def task_started(self, job, task, worker, supervisor, section) -> None:
        step = task.ordinal + 1
        self._push(job, {"type": "flow", "source": supervisor.id, "target": worker.id, "label": f"step {step}"})
        self._push(job, {"type": "step", "step_id": step, "status": "running", "attempt": task.attempts + 1,
                         "assignee": worker.id})
        self._push(job, {"type": "agent_status", "agent": worker.id, "status": "working"})

    @_safe
    def submitted(self, job, task, worker, supervisor, n: int, items: list[dict] | None = None,
                  round_: int = 0) -> None:
        self._push(job, {"type": "flow", "source": worker.id, "target": supervisor.id, "label": "result"})
        if items:  # the worker's report, as it is sent to the supervisor
            head = "Temuan" if job.type == "review" else "Usulan edit"
            again = " (setelah diperbaiki)" if round_ else ""
            body = "\n".join(f"{i}. {_describe(x)}" for i, x in enumerate(items, 1))
            self._push(job, {"type": "output", "agent": worker.id, "step_id": task.ordinal + 1, "attempt": round_ + 1,
                             "output": _cap(f"{head}{again} — {len(items)}:\n{body}")})
        self._push(job, {"type": "agent_status", "agent": worker.id, "status": "idle"})
        self._push(job, {"type": "agent_status", "agent": supervisor.id, "status": "reviewing"})

    @_safe
    def revise_requested(self, job, task, worker, supervisor, items: list[tuple[dict, str]]) -> None:
        body = "\n".join(f"{i}. {_describe(x)} → {fb or 'perbaiki'}" for i, (x, fb) in enumerate(items, 1))
        self._push(job, {"type": "flow", "source": supervisor.id, "target": worker.id, "label": "revise"})
        self._push(job, {"type": "review", "step_id": task.ordinal + 1, "verdict": "revise",
                         "feedback": _cap(f"Minta perbaiki {len(items)}:\n{body}")})

    @_safe
    def task_finished(self, job, task, worker, supervisor, n_valid: int, rejected: list[dict] | None = None,
                      held: list[dict] | None = None) -> None:
        step = task.ordinal + 1
        rejected, held = rejected or [], held or []
        lines = [f"Diterima {n_valid} · Ditolak {len(rejected)} · Perlu dicek {len(held)}"]
        lines += [f"✗ {_describe(x)} — {_why(x)}" for x in rejected]
        lines += [f"? {_describe(x)} — {_why(x)}" for x in held]
        self._push(job, {"type": "review", "step_id": step, "verdict": "approve", "feedback": _cap("\n".join(lines))})
        self._push(job, {"type": "agent_status", "agent": worker.id, "status": "idle"})
        self._push(job, {"type": "agent_status", "agent": supervisor.id, "status": "idle"})
        self._push(job, {"type": "step", "step_id": step, "status": "done", "attempt": task.attempts + 1,
                         "assignee": worker.id})
        if n_valid:
            self._push(job, {"type": "note", "agent": supervisor.id,
                             "message": f"bagian {step}: {n_valid} temuan diterima"})

    # ---------------------------------------------------------- job events
    @_safe
    def on_job_event(self, job: Job, event: str, payload: dict) -> None:
        if job is None or job.type not in ("review", "revision", "instructed_revision", "update"):
            return
        sup = (registry.by_role("supervisor")[:1] or [None])[0]
        agent = sup.id if sup else ""
        if event == "started":
            self._session(job)
        elif event == "paused":
            self._push(job, {"type": "note", "agent": agent,
                             "message": "kuota Claude habis — review dijeda dan akan lanjut otomatis"})
        elif event == "resumed":
            self._push(job, {"type": "note", "agent": agent, "message": "review dilanjutkan"})
        elif event == "task_failed":
            self._push(job, {"type": "error", "message": f"bagian gagal: {payload.get('error', '')}"})
        elif event == "finalizing":
            self._push(job, {"type": "agent_status", "agent": agent, "status": "reviewing"})
            self._push(job, {"type": "note", "agent": agent, "message": "cek konsistensi antarbab" if job.type == "review"
                             else "menerapkan perubahan ke DOCX"})
        elif event in ("finished", "cancelled"):
            self._push(job, {"type": "agent_status", "agent": agent, "status": "idle"})
            if job.type == "review":
                counts = get_store().count_findings(job.id)
                msg = (f"review {payload.get('status', event)}: {counts.get('valid', 0)} temuan valid, "
                       f"{counts.get('needs_human', 0)} perlu dicek, {counts.get('rejected', 0)} ditolak")
            else:
                ch = get_store().get_changes(job_id=job.id)
                n = {k: sum(c.status == k for c in ch) for k in ("applied", "needs_human", "rejected", "suggestion")}
                fresh = get_store().get_job(job.id) or job
                where = "versi baru dibuat" if fresh.params.get("result_version_id") else "tidak ada versi baru"
                msg = (f"revisi {payload.get('status', event)}: {n['applied']} perubahan diterapkan, "
                       f"{n['needs_human']} perlu dicek, {n['rejected']} ditolak, {n['suggestion']} saran — {where}")
            self._push(job, {"type": "note", "agent": agent, "message": msg})
            self._push(job, {"type": "stage", "stage": "done"})


def install(orchestrator) -> OfficeBridge:
    """Connect document jobs to the office visualiser (idempotent)."""
    global bridge, _listening
    bridge = OfficeBridge(orchestrator)
    if not _listening:
        runner.on_event(lambda job, event, payload: bridge and bridge.on_job_event(job, event, payload))
        _listening = True
    return bridge
