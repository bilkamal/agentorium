"""Agentorium client — drive the office visualiser from any Python workflow.

Zero dependencies (urllib only). Usage:

    from agentorium import Office
    office = Office("http://127.0.0.1:8000", token=None)
    session = office.session("My LangGraph run")
    session.register("planner", "Planner", role="supervisor", color="#1a9f5c")
    session.register("coder", "Coder", role="worker")
    session.status("coder", "writing code")          # character walks to its desk and types
    session.flow("planner", "coder", "task")         # envelope flies between the two
    session.say("planner", "Done! Here is the result ...")   # appears in the chat panel
    session.status("coder", "idle")                  # back to the lounge
    for msg in session.user_messages():              # blocking generator over the event stream
        ...

Event types accepted by the server: register_agent, agent_status, flow, chat, stage, plan,
step, review, output, note, error, busy. Everything is best-effort: failures never raise
inside your workflow unless `strict=True`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

__all__ = ["Office", "Session"]


class Office:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", token: str | None = None, strict: bool = False, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.strict = strict
        self.timeout = timeout

    # ------------------------------------------------------------ http
    def _request(self, method: str, path: str, body: dict | None = None, stream: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("X-Office-Token", self.token)
        try:
            resp = urllib.request.urlopen(req, timeout=None if stream else self.timeout)
            return resp if stream else json.loads(resp.read() or b"null")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            if self.strict or stream:
                raise
            print(f"[agentorium] {method} {path} failed: {exc}")
            return None

    def session(self, title: str = "External workflow") -> "Session":
        data = self._request("POST", "/api/sessions", {"kind": "external", "title": title})
        return Session(self, data["id"] if data else None)

    def attach(self, session_id: str) -> "Session":
        return Session(self, session_id)


class Session:
    def __init__(self, office: Office, session_id: str | None):
        self.office = office
        self.id = session_id

    @property
    def url(self) -> str:
        return f"{self.office.base_url}/?session={self.id}"

    # ------------------------------------------------------------ events
    def emit(self, type_: str, **data) -> None:
        if not self.id:
            return
        self.office._request("POST", f"/api/sessions/{self.id}/events", {"type": type_, **data})

    def emit_many(self, events: list[dict]) -> None:
        if self.id:
            self.office._request("POST", f"/api/sessions/{self.id}/events", {"events": events})

    def register(self, agent_id: str, name: str, role: str = "worker", color: str | None = None, hair: str | None = None, skills: str = "") -> None:
        self.emit("register_agent", id=agent_id, name=name, role=role, **({"color": color} if color else {}), **({"hair": hair} if hair else {}), skills=skills)

    def status(self, agent_id: str, status: str = "idle") -> None:
        """Any text other than 'idle' sends the agent to its desk (shown in a speech bubble)."""
        self.emit("agent_status", agent=agent_id, status=status or "idle")

    def idle(self, agent_id: str) -> None:
        self.status(agent_id, "idle")

    def flow(self, source: str, target: str, label: str = "") -> None:
        """Envelope animation from one agent to another ('user' = the office door)."""
        self.emit("flow", source=source, target=target, label=label)

    def say(self, agent_id: str, content: str) -> None:
        """Post a message into the chat panel as this agent."""
        self.emit("chat", agent=agent_id, role="assistant", content=content)

    def stage(self, stage: str) -> None:
        """One of discussing, planning, awaiting_approval, executing, done (drives the stepper)."""
        self.emit("stage", stage=stage)

    def plan(self, objective: str, steps: list[dict], acceptance_criteria: list[str] | None = None, assumptions: list[str] | None = None) -> None:
        """steps: [{"id": 1, "title": ..., "assignee": "<agent id>", "instructions": ..., "deliverable": ...}]"""
        self.emit("plan", status="agreed", plan={"objective": objective, "steps": steps,
                                                "acceptance_criteria": acceptance_criteria or [], "assumptions": assumptions or []})

    def step(self, step_id: int, status: str, attempt: int = 1, assignee: str | None = None) -> None:
        """status: running | revising | done"""
        self.emit("step", step_id=step_id, status=status, attempt=attempt, assignee=assignee)

    def review(self, step_id: int, verdict: str, score: int | None = None, feedback: str = "", attempt: int = 1) -> None:
        self.emit("review", step_id=step_id, verdict=verdict, score=score, feedback=feedback, attempt=attempt)

    def output(self, agent_id: str, step_id: int, output: str, attempt: int = 1) -> None:
        self.emit("output", agent=agent_id, step_id=step_id, output=output, attempt=attempt)

    def note(self, agent_id: str, message: str) -> None:
        self.emit("note", agent=agent_id, message=message)

    def busy(self, busy: bool = True) -> None:
        self.emit("busy", busy=busy)

    # ------------------------------------------------------------ inbound
    def events(self) -> Iterator[dict]:
        """Blocking generator over the session's Server-Sent Events (snapshot first)."""
        resp = self.office._request("GET", f"/api/sessions/{self.id}/events", stream=True)
        event_type, payload = None, []
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                payload.append(line[5:].strip())
            elif line == "" and event_type:
                try:
                    yield {"event": event_type, **json.loads("".join(payload))} if event_type != "snapshot" else {"event": "snapshot", "snapshot": json.loads("".join(payload))}
                except json.JSONDecodeError:
                    pass
                event_type, payload = None, []

    def user_messages(self) -> Iterator[str]:
        """Yield each message the user types into the chat panel (blocks forever)."""
        for event in self.events():
            if event.get("event") == "chat" and event.get("role") == "user":
                yield event.get("content", "")

    def documents(self) -> list[dict]:
        data = self.office._request("GET", f"/api/sessions/{self.id}")
        return (data or {}).get("documents", [])

    def document_text(self, doc_id: str) -> str:
        data = self.office._request("GET", f"/api/sessions/{self.id}/documents/{doc_id}/text")
        return (data or {}).get("text", "")
