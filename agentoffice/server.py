"""FastAPI application: REST + SSE API, external event ingestion, and the static web UI."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, settings
from .documents import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, DocumentError, extract_text
from .orchestrator import EXTERNAL_EVENT_TYPES, Orchestrator, Session
from .registry import COLORS, HAIR, ROLES, RegistryError, registry

app = FastAPI(title="Agent Office", version="0.2.0")
orchestrator = Orchestrator()
WEB_DIR = ROOT / "web"
SDK_DIR = ROOT / "sdk"
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
if SDK_DIR.exists():
    app.mount("/sdk", StaticFiles(directory=str(SDK_DIR)), name="sdk")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    lang: str | None = None


class FeedbackIn(BaseModel):
    feedback: str = ""
    lang: str | None = None


def _set_lang(s: Session, lang: str | None) -> None:
    if lang in ("id", "en"):
        s.lang = lang


class SessionIn(BaseModel):
    kind: str = "workflow"
    title: str | None = None


def _session(session_id: str) -> Session:
    s = orchestrator.get(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return s


def _start_or_409(s: Session, coro) -> dict:
    if not orchestrator.start(s, coro):
        coro.close()
        raise HTTPException(409, "session is busy; wait for the current step to finish or cancel it")
    return {"ok": True}


def _check_token(token: str | None) -> None:
    if settings.api_token and token != settings.api_token:
        raise HTTPException(401, "invalid or missing X-Office-Token")


# ----------------------------------------------------------------- pages
@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/config")
async def config():
    return {
        "agents": registry.public(),
        "roles": ROLES,
        "providers": ["anthropic", "openai", "mock"],
        "colors": COLORS, "hairs": HAIR,
        "max_revisions": settings.max_revisions,
        "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
        "lang": settings.lang,
        "token_required": bool(settings.api_token),
    }


# ---------------------------------------------------------------- agents
@app.get("/api/agents")
async def list_agents():
    return registry.public()


@app.post("/api/agents")
async def upsert_agent(body: dict = Body(...)):
    try:
        agent = registry.upsert(body)
    except (RegistryError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    _broadcast_roster()
    return agent.public()


@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str):
    try:
        registry.delete(agent_id)
    except RegistryError as exc:
        raise HTTPException(400, str(exc)) from exc
    _broadcast_roster()
    return {"ok": True}


def _broadcast_roster() -> None:
    """Tell every open workflow session that the team changed."""
    for s in orchestrator.sessions.values():
        if s.kind == "workflow":
            s.emit("roster", agents=registry.public())


# -------------------------------------------------------------- sessions
@app.get("/api/sessions")
async def list_sessions():
    items = sorted(orchestrator.sessions.values(), key=lambda s: s.created_at, reverse=True)
    return [{"id": s.id, "kind": s.kind, "title": s.title, "stage": s.stage, "created_at": s.created_at} for s in items]


@app.post("/api/sessions", status_code=201)
async def create_session(body: SessionIn | None = None, x_office_token: str | None = Header(default=None)):
    body = body or SessionIn()
    if body.kind not in ("workflow", "external"):
        raise HTTPException(400, "kind must be 'workflow' or 'external'")
    if body.kind == "external":
        _check_token(x_office_token)
    return orchestrator.create_session(kind=body.kind, title=(body.title or None)).to_dict()


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    return _session(session_id).to_dict()


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    _session(session_id)
    orchestrator.delete(session_id)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/chat", status_code=202)
async def chat(session_id: str, body: ChatIn):
    s = _session(session_id)
    _set_lang(s, body.lang)
    if s.kind == "external":  # just relay to the external workflow (it listens on the event stream)
        s.add_chat("user", body.message.strip())
        s.save()
        return {"ok": True}
    return _start_or_409(s, orchestrator.handle_user_message(s, body.message.strip()))


@app.post("/api/sessions/{session_id}/plan/approve", status_code=202)
async def approve_plan(session_id: str, body: FeedbackIn | None = None):
    s = _session(session_id)
    _set_lang(s, body.lang if body else None)
    if s.stage != "awaiting_approval":
        raise HTTPException(400, "no plan awaiting approval")
    s.add_chat("user", "✅ Rencana disetujui" if s.lang == "id" else "✅ Plan approved")
    if s.kind == "external":
        s.save()
        return {"ok": True}
    return _start_or_409(s, orchestrator.run_execution(s))


@app.post("/api/sessions/{session_id}/plan/reject", status_code=202)
async def reject_plan(session_id: str, body: FeedbackIn):
    s = _session(session_id)
    _set_lang(s, body.lang)
    if s.stage != "awaiting_approval":
        raise HTTPException(400, "no plan awaiting approval")
    feedback = body.feedback.strip() or ("Tolong revisi rencananya." if s.lang == "id" else "Please revise the plan.")
    s.add_chat("user", (f"🔁 Permintaan perubahan: {feedback}" if s.lang == "id" else f"🔁 Change request: {feedback}"))
    if s.kind == "external":
        s.save()
        return {"ok": True}
    return _start_or_409(s, orchestrator.run_planning(s, feedback=feedback))


@app.post("/api/sessions/{session_id}/cancel")
async def cancel(session_id: str):
    return {"cancelled": orchestrator.cancel(_session(session_id))}


# ------------------------------------------------------ external workflows
@app.post("/api/sessions/{session_id}/events", status_code=202)
async def push_event(session_id: str, body: dict = Body(...), x_office_token: str | None = Header(default=None)):
    """Ingest one event (or {"events": [...]}) from an external workflow. See sdk/ for clients."""
    _check_token(x_office_token)
    s = _session(session_id)
    if s.kind != "external":
        raise HTTPException(400, "events can only be pushed into sessions created with kind='external'")
    events = body.get("events") if isinstance(body.get("events"), list) else [body]
    for event in events:  # validate the whole batch before applying any of it
        if not isinstance(event, dict) or event.get("type") not in EXTERNAL_EVENT_TYPES:
            raise HTTPException(400, f"invalid event {event!r}; allowed types: {sorted(EXTERNAL_EVENT_TYPES)}")
    try:
        accepted = [orchestrator.push_external(s, event)["id"] for event in events]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"accepted": accepted, "allowed_types": sorted(EXTERNAL_EVENT_TYPES)}


# ------------------------------------------------------------- documents
@app.post("/api/sessions/{session_id}/documents", status_code=201)
async def upload_document(session_id: str, file: UploadFile = File(...)):
    s = _session(session_id)
    name = Path(file.filename or "upload").name
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Only {', '.join(sorted(ALLOWED_EXTENSIONS))} files are accepted")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file larger than 25 MB")

    doc_id = uuid.uuid4().hex[:10]
    folder = settings.data_dir / "uploads" / s.id
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / f"{doc_id}{Path(name).suffix.lower()}"
    stored.write_bytes(data)
    try:
        text = await asyncio.to_thread(extract_text, stored, name)
    except DocumentError as exc:
        stored.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc

    doc = {"id": doc_id, "name": name, "chars": len(text), "bytes": len(data), "text": text}
    s.documents.append(doc)
    s.save()
    public = {k: v for k, v in doc.items() if k != "text"}
    s.emit("document", action="added", document=public)
    return public


@app.get("/api/sessions/{session_id}/documents/{doc_id}/text")
async def document_text(session_id: str, doc_id: str, x_office_token: str | None = Header(default=None)):
    """Extracted text of an upload — lets external workflows read what the user shared."""
    _check_token(x_office_token)
    s = _session(session_id)
    doc = next((d for d in s.documents if d["id"] == doc_id), None)
    if not doc:
        raise HTTPException(404, "document not found")
    return {"id": doc_id, "name": doc["name"], "text": doc["text"]}


@app.delete("/api/sessions/{session_id}/documents/{doc_id}")
async def delete_document(session_id: str, doc_id: str):
    s = _session(session_id)
    before = len(s.documents)
    s.documents = [d for d in s.documents if d["id"] != doc_id]
    if len(s.documents) == before:
        raise HTTPException(404, "document not found")
    for f in (settings.data_dir / "uploads" / s.id).glob(f"{doc_id}.*"):
        f.unlink(missing_ok=True)
    s.save()
    s.emit("document", action="removed", document={"id": doc_id})
    return {"ok": True}


# ---------------------------------------------------------------- events
@app.get("/api/sessions/{session_id}/events")
async def events(session_id: str):
    """Server-Sent Events stream of everything that happens in the session."""
    s = _session(session_id)
    queue: asyncio.Queue = asyncio.Queue()
    s.subscribers.append(queue)

    async def generator():
        try:
            yield f"event: snapshot\ndata: {json.dumps(s.to_dict(), ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            if queue in s.subscribers:
                s.subscribers.remove(queue)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
