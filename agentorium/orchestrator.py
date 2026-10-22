"""Workflow engine: sessions, event bus, the client/supervisor/worker state machine,
and the ingest path for external workflows that only use the office as a visualiser.

Stages (built-in workflow):
  discussing        user <-> client; the client decides when the task is clear
  planning          supervisor drafts plan, client reviews, supervisor revises (max 1 round)
  awaiting_approval plan presented; user approves / asks for changes
  executing         steps run in dependency waves (parallel inside a wave); supervisor reviews each
  done              client delivered the result; the user can start a new task
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from . import agents as P
from .config import settings
from .llm import LLMClient, LLMError
from .registry import AgentDef, RegistryError, registry

STAGES = ("discussing", "planning", "awaiting_approval", "executing", "done")
MAX_EVENTS = 400
EXTERNAL_EVENT_TYPES = {"register_agent", "agent_status", "flow", "chat", "stage", "plan", "step", "review", "output", "note", "error", "busy"}


def now() -> float:
    return time.time()


class Session:
    def __init__(self, session_id: str | None = None, kind: str = "workflow", title: str | None = None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.kind = kind                      # "workflow" (built-in) | "external" (driven via /events)
        self.created_at = now()
        self.title = title or "New session"
        self.stage = "discussing"
        self.chat: list[dict] = []
        self.documents: list[dict] = []
        self.plan: dict | None = None
        self.results: list[dict] = []
        self.events: list[dict] = []
        self.roster: list[dict] = []          # external sessions: agents registered via events
        self.agent_status: dict[str, str] = {}
        self.busy = False
        self.error: str | None = None
        self.lang = settings.lang          # UI language of the last interaction (mock replies, system notes)
        # runtime only
        self.subscribers: list[asyncio.Queue] = []
        self.task: asyncio.Task | None = None

    # ------------------------------------------------------------ persistence
    def agents_public(self) -> list[dict]:
        return self.roster if self.kind == "external" else registry.public()

    def to_dict(self, include_doc_text: bool = False) -> dict:
        docs = [d if include_doc_text else {k: v for k, v in d.items() if k != "text"} for d in self.documents]
        return {
            "id": self.id, "kind": self.kind, "created_at": self.created_at, "title": self.title, "stage": self.stage,
            "chat": self.chat, "documents": docs, "plan": self.plan, "results": self.results, "events": self.events,
            "roster": self.roster, "agents": self.agents_public(), "agent_status": self.agent_status,
            "busy": self.busy, "error": self.error, "lang": self.lang,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        s = cls(data["id"], kind=data.get("kind", "workflow"))
        for key in ("created_at", "title", "stage", "chat", "documents", "plan", "results", "events", "roster", "agent_status", "lang"):
            setattr(s, key, data.get(key, getattr(s, key)))
        s.error = data.get("error")
        return s

    @property
    def path(self) -> Path:
        return settings.data_dir / "sessions" / f"{self.id}.json"

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(include_doc_text=True), ensure_ascii=False, indent=1))
        tmp.replace(self.path)

    # -------------------------------------------------------------- event bus
    def emit(self, type_: str, **data) -> dict:
        event = {"id": uuid.uuid4().hex[:8], "ts": now(), "type": type_, **data}
        self.events.append(event)
        del self.events[:-MAX_EVENTS]
        for q in list(self.subscribers):
            q.put_nowait(event)
        return event

    def set_status(self, agent_id: str, status: str, note: str = "") -> None:
        self.agent_status[agent_id] = status
        self.emit("agent_status", agent=agent_id, status=status, note=note)

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.emit("stage", stage=stage)

    def add_chat(self, role: str, content: str, author: str | None = None) -> None:
        msg = {"role": role, "content": content, "ts": now(), "author": author}
        self.chat.append(msg)
        self.emit("chat", **msg)


class Orchestrator:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self._clients: dict[str, tuple[tuple, LLMClient]] = {}
        self._load_sessions()

    # --------------------------------------------------------- session mgmt
    def _load_sessions(self) -> None:
        folder = settings.data_dir / "sessions"
        folder.mkdir(parents=True, exist_ok=True)
        for file in sorted(folder.glob("*.json")):
            try:
                s = Session.from_dict(json.loads(file.read_text()))
                if s.kind == "workflow" and s.stage in ("planning", "executing"):  # interrupted by a restart
                    s.stage = "discussing" if s.plan is None else "awaiting_approval"
                s.agent_status = {k: "idle" for k in s.agent_status}
                self.sessions[s.id] = s
            except (json.JSONDecodeError, KeyError):
                continue

    def create_session(self, kind: str = "workflow", title: str | None = None) -> Session:
        s = Session(kind=kind, title=title)
        self.sessions[s.id] = s
        s.save()
        return s

    def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def delete(self, session_id: str) -> None:
        s = self.sessions.pop(session_id, None)
        if s:
            if s.task and not s.task.done():
                s.task.cancel()
            s.path.unlink(missing_ok=True)

    # ------------------------------------------------------- LLM wrapper
    def client_for(self, agent: AgentDef) -> LLMClient:
        """One LLM client per agent, rebuilt when its provider settings change."""
        cached = self._clients.get(agent.id)
        if cached and cached[0] == agent.fingerprint():
            return cached[1]
        client = LLMClient(agent)
        self._clients[agent.id] = (agent.fingerprint(), client)
        return client

    async def ask(self, s: Session, agent: AgentDef, messages: list[dict], *, purpose: str, status: str) -> str:
        """Call one agent, publishing its status while it works."""
        s.set_status(agent.id, status, note=purpose)
        try:
            return await self.client_for(agent).complete(P.system(agent, registry.list()), messages, purpose=purpose, lang=s.lang)
        finally:
            s.set_status(agent.id, "idle")

    async def ask_json(self, s: Session, agent: AgentDef, messages: list[dict], *, purpose: str, status: str) -> tuple[dict | None, str]:
        """Like ask() but returns (parsed_json, raw). One retry nudging the model toward JSON."""
        raw = await self.ask(s, agent, messages, purpose=purpose, status=status)
        data = P.extract_json(raw)
        if data is None:
            retry = messages + [{"role": "assistant", "content": raw},
                                {"role": "user", "content": "That was not valid JSON. Reply with the requested JSON object only."}]
            raw = await self.ask(s, agent, retry, purpose=purpose, status=status)
            data = P.extract_json(raw)
        return data, raw

    # ------------------------------------------------------- entry points
    def start(self, s: Session, coro) -> bool:
        """Run a workflow coroutine in the background; False if the session is busy."""
        if s.busy:
            return False
        s.busy = True
        s.error = None

        async def runner():
            try:
                await coro
            except asyncio.CancelledError:
                s.emit("error", message="Cancelled by user")
                raise
            except (LLMError, RegistryError) as exc:
                s.error = str(exc)
                s.emit("error", message=str(exc))
                if s.stage in ("planning", "executing"):
                    s.set_stage("discussing" if s.plan is None else "awaiting_approval")
            except Exception as exc:  # keep the server alive on unexpected bugs
                s.error = f"Internal error: {exc!r}"
                s.emit("error", message=s.error)
            finally:
                for k, v in list(s.agent_status.items()):
                    if v != "idle":
                        s.set_status(k, "idle")
                s.busy = False
                s.emit("busy", busy=False)
                s.save()

        s.task = asyncio.create_task(runner())
        s.emit("busy", busy=True)
        return True

    def cancel(self, s: Session) -> bool:
        if s.task and not s.task.done():
            s.task.cancel()
            return True
        return False

    # ---------------------------------------------------------- workflows
    async def handle_user_message(self, s: Session, text: str) -> None:
        s.add_chat("user", text)
        if s.title == "New session":
            s.title = text.strip()[:60]
        if s.stage == "done":
            s.set_stage("discussing")  # a new request after delivery starts a fresh cycle
        s.save()

        client, supervisor, team = registry.client(), registry.supervisor(), registry.list()
        purpose = "c_approval" if s.stage == "awaiting_approval" else "c_chat"
        messages = P.c_chat_messages(s.chat, s.documents, s.stage, s.plan, team, supervisor)
        data, raw = await self.ask_json(s, client, messages, purpose=purpose, status="thinking")
        reply = (data or {}).get("reply") or raw
        action = (data or {}).get("action", "chat")
        s.add_chat("assistant", reply, author=client.id)
        s.save()

        if action == "plan" and s.stage == "discussing":
            await self.run_planning(s)
        elif action == "execute" and s.stage == "awaiting_approval":
            await self.run_execution(s)
        elif action == "replan" and s.stage == "awaiting_approval":
            await self.run_planning(s, feedback=(data or {}).get("feedback") or text)

    async def run_planning(self, s: Session, feedback: str | None = None) -> None:
        client, supervisor, workers, team = registry.client(), registry.supervisor(), registry.workers(), registry.list()
        if not workers:
            raise RegistryError("No worker agents — add one in Settings")
        s.set_stage("planning")
        s.emit("flow", source=client.id, target=supervisor.id, label="brief" if not feedback else "change request")
        previous = s.plan if feedback else None

        plan, raw = await self.ask_json(s, supervisor, P.b_plan_messages(s.chat, s.documents, feedback, previous, workers, team, client),
                                        purpose="b_plan", status="planning")
        if not plan or not plan.get("steps"):
            raise LLMError(f"{supervisor.name} did not return a usable plan: {raw[:200]}")
        plan = _normalise_plan(plan, workers)
        s.emit("flow", source=supervisor.id, target=client.id, label="draft plan")
        s.emit("plan", plan=plan, status="draft")

        review, _ = await self.ask_json(s, client, P.c_plan_review_messages(s.chat, plan, team, client),
                                        purpose="c_plan_review", status="reviewing plan")
        if review and not review.get("approved", True):
            s.emit("flow", source=client.id, target=supervisor.id, label="revise plan")
            s.emit("note", agent=client.id, message=f"Plan feedback: {review.get('feedback', '')}")
            revised, _ = await self.ask_json(s, supervisor, P.b_plan_messages(s.chat, s.documents, review.get("feedback"), plan, workers, team, client),
                                             purpose="b_plan", status="revising plan")
            if revised and revised.get("steps"):
                plan = _normalise_plan(revised, workers)
            s.emit("flow", source=supervisor.id, target=client.id, label="revised plan")

        s.plan = plan
        s.results = []
        s.emit("plan", plan=plan, status="agreed")
        presentation = await self.ask(s, client, P.c_present_plan_messages(s.chat, plan, team, client),
                                      purpose="c_present_plan", status="writing")
        s.add_chat("assistant", presentation, author=client.id)
        s.set_stage("awaiting_approval")
        s.save()

    async def run_execution(self, s: Session) -> None:
        if not s.plan:
            raise LLMError("No plan to execute")
        client, supervisor, team = registry.client(), registry.supervisor(), registry.list()
        s.set_stage("executing")
        s.results = []
        s.emit("flow", source=client.id, target=supervisor.id, label="go")

        done: dict[int, dict] = {}
        for wave in _waves(s.plan["steps"]):
            results = await asyncio.gather(*(self._run_step(s, step, supervisor, team, client, done) for step in wave))
            for step, result in zip(wave, results):
                done[step["id"]] = result
            s.results = [done[k] for k in sorted(done)]
            s.save()

        s.emit("flow", source=supervisor.id, target=client.id, label="all approved")
        delivery = await self.ask(s, client, P.c_deliver_messages(s.chat, s.plan, s.results, team, client),
                                  purpose="c_deliver", status="writing delivery")
        s.add_chat("assistant", delivery, author=client.id)
        s.set_stage("done")
        s.save()

    async def _run_step(self, s: Session, step: dict, supervisor: AgentDef, team: list[AgentDef], client: AgentDef, done: dict[int, dict]) -> dict:
        """Worker executes a step; the supervisor reviews; revise up to settings.max_revisions times."""
        worker = registry.get(step["assignee"]) or registry.workers()[0]
        prior = [done[k] for k in sorted(done)]
        feedback: str | None = None
        output, verdict, attempt = "", {}, 1
        s.emit("step", step_id=step["id"], status="running", attempt=1, assignee=worker.id)
        for attempt in range(1, settings.max_revisions + 2):
            s.emit("flow", source=supervisor.id, target=worker.id, label="revise" if feedback else f"step {step['id']}")
            output = await self.ask(s, worker, P.a_execute_messages(s.chat, s.plan, step, prior, s.documents, feedback, output, team, client),
                                    purpose="a_execute", status=f"working on step {step['id']}")
            s.emit("output", step_id=step["id"], attempt=attempt, output=output, agent=worker.id)
            s.emit("flow", source=worker.id, target=supervisor.id, label="result")
            verdict, raw = await self.ask_json(s, supervisor, P.b_review_messages(s.plan, step, output, s.documents, team, worker),
                                               purpose="b_review", status=f"reviewing step {step['id']}")
            verdict = verdict or {"verdict": "approve", "score": None, "feedback": f"(unparseable review) {raw[:200]}"}
            s.emit("review", step_id=step["id"], attempt=attempt, **verdict)
            if str(verdict.get("verdict", "approve")).lower() != "revise":
                break
            feedback = verdict.get("feedback") or "Improve the output."
            s.emit("step", step_id=step["id"], status="revising", attempt=attempt + 1, assignee=worker.id)
        s.emit("step", step_id=step["id"], status="done", attempt=attempt, assignee=worker.id)
        return {"step_id": step["id"], "title": step["title"], "assignee": worker.id, "assignee_name": worker.name,
                "output": output, "verdict": verdict.get("verdict", "approve"), "score": verdict.get("score"),
                "feedback": verdict.get("feedback", "")}

    # ---------------------------------------------------------- external workflows
    def push_external(self, s: Session, event: dict) -> dict:
        """Ingest one event from an external workflow and broadcast it to the UI."""
        type_ = str(event.get("type", ""))
        if type_ not in EXTERNAL_EVENT_TYPES:
            raise ValueError(f"unknown event type '{type_}'; allowed: {sorted(EXTERNAL_EVENT_TYPES)}")
        data = {k: v for k, v in event.items() if k not in ("type", "id", "ts")}
        if type_ == "register_agent":
            agent = {
                "id": str(event.get("id") or data.get("agent") or uuid.uuid4().hex[:8])[:40],
                "name": str(data.get("name") or "Agent")[:40],
                "role": str(data.get("role") or "worker")[:20],
                "color": str(data.get("color") or "#4f6df5")[:9],
                "hair": str(data.get("hair") or "#2b2b2b")[:9],
                "skills": str(data.get("skills") or "")[:200],
            }
            s.roster = [a for a in s.roster if a["id"] != agent["id"]] + [agent]
            s.agent_status.setdefault(agent["id"], "idle")
            s.save()
            return s.emit("register_agent", agent=agent)
        if type_ == "agent_status":
            s.agent_status[str(data.get("agent"))] = str(data.get("status") or "idle")[:60]
        elif type_ == "chat":
            role = "assistant" if data.get("role") != "user" else "user"
            s.add_chat(role, str(data.get("content", ""))[:20000], author=data.get("agent"))
            s.save()
            return s.events[-1]
        elif type_ == "stage":
            s.stage = str(data.get("stage") or "discussing")[:40]
        elif type_ == "plan":
            s.plan = data.get("plan") if isinstance(data.get("plan"), dict) else None
        elif type_ == "busy":
            s.busy = bool(data.get("busy"))
        s.save()
        return s.emit(type_, **data)


def _normalise_plan(plan: dict, workers: list[AgentDef]) -> dict:
    """Guarantee ids, titles, valid assignees and acyclic dependencies."""
    ids = [w.id for w in workers]
    steps = []
    for i, step in enumerate(plan.get("steps", []), start=1):
        if not isinstance(step, dict):
            step = {"title": str(step)}
        assignee = str(step.get("assignee") or "")
        if assignee not in ids:  # also accept a worker *name*; otherwise round-robin
            by_name = next((w.id for w in workers if w.name.lower() == assignee.lower()), None)
            assignee = by_name or ids[(i - 1) % len(ids)]
        deps = []
        for d in step.get("depends_on", []) or []:
            try:
                d = int(d)
            except (TypeError, ValueError):
                continue
            if 0 < d < i:
                deps.append(d)
        steps.append({
            "id": i,
            "title": str(step.get("title") or f"Step {i}"),
            "instructions": str(step.get("instructions") or step.get("description") or ""),
            "deliverable": str(step.get("deliverable") or ""),
            "assignee": assignee,
            "depends_on": sorted(set(deps)),
        })
    return {
        "objective": str(plan.get("objective", "")),
        "assumptions": [str(a) for a in plan.get("assumptions", []) or []],
        "steps": steps,
        "acceptance_criteria": [str(c) for c in plan.get("acceptance_criteria", []) or []],
    }


def _waves(steps: list[dict]) -> list[list[dict]]:
    """Group steps into dependency waves; steps in the same wave run concurrently."""
    remaining = list(steps)
    finished: set[int] = set()
    waves: list[list[dict]] = []
    while remaining:
        wave = [st for st in remaining if all(d in finished for d in st["depends_on"])] or remaining[:1]
        waves.append(wave)
        finished.update(st["id"] for st in wave)
        remaining = [st for st in remaining if st not in wave]
    return waves
