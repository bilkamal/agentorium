"""Provider-agnostic LLM client.

Each agent owns one `LLMClient` built from its `AgentConfig`:
  * anthropic -> official Anthropic SDK (streaming, adaptive thinking, fallbacks)
  * openai    -> any OpenAI-compatible `/chat/completions` endpoint via httpx
  * mock      -> deterministic canned replies so the workflow runs offline
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx

from .config import settings

# Claude models that accept the server-side `fallbacks: "default"` parameter.
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")


class LLMError(Exception):
    """Raised for any provider failure; message is safe to show in the UI."""


class LLMClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._anthropic = None
        self._http: httpx.AsyncClient | None = None

    async def complete(self, system: str, messages: list[dict], *, purpose: str = "", lang: str | None = None) -> str:
        """Return the assistant text for `messages` ([{role, content}] with user/assistant roles)."""
        if self.cfg.provider == "anthropic":
            return await self._complete_anthropic(system, messages)
        if self.cfg.provider == "openai":
            return await self._complete_openai(system, messages)
        return await mock_complete(self.cfg, purpose, messages, lang or settings.lang)

    # ------------------------------------------------------------------ anthropic
    async def _complete_anthropic(self, system: str, messages: list[dict]) -> str:
        import anthropic

        if self._anthropic is None:
            self._anthropic = anthropic.AsyncAnthropic(
                api_key=self.cfg.api_key or None,
                base_url=self.cfg.base_url or None,
                max_retries=3,
            )
        client = self._anthropic
        kwargs: dict = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            # Stable system prompt first so prompt caching can reuse it across turns.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if self.cfg.effort:
            kwargs["output_config"] = {"effort": self.cfg.effort}

        use_fallbacks = settings.anthropic_fallbacks and self.cfg.model.startswith(FALLBACK_MODELS)
        try:
            if use_fallbacks:
                # Safety-classifier refusals are retried server-side on a fallback model.
                async with client.beta.messages.stream(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                ) as stream:
                    response = await stream.get_final_message()
            else:
                async with client.messages.stream(**kwargs) as stream:
                    response = await stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise LLMError(f"{self.cfg.name}: invalid Anthropic API key ({exc.status_code})") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError(f"{self.cfg.name}: rate limited by Anthropic, try again shortly") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"{self.cfg.name}: Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"{self.cfg.name}: cannot reach Anthropic API ({exc})") from exc

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            why = getattr(details, "explanation", None) or "request declined by safety classifier"
            raise LLMError(f"{self.cfg.name}: refusal — {why}")
        text = "".join(block.text for block in response.content if block.type == "text")
        if response.stop_reason == "max_tokens":
            text += "\n\n[output truncated: max_tokens reached]"
        return text

    # ------------------------------------------------------------- openai-compatible
    async def _complete_openai(self, system: str, messages: list[dict]) -> str:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))
        base = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        body: dict = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": self.cfg.max_tokens,
        }
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._http.post(f"{base}/chat/completions", json=body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = LLMError(f"{self.cfg.name}: HTTP {resp.status_code} from {base}")
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise LLMError(f"{self.cfg.name}: HTTP {resp.status_code} from {base}: {resp.text[:300]}")
            return _extract_openai_text(resp.json(), self.cfg.name)
        raise LLMError(f"{self.cfg.name}: request failed after retries ({last_error})")


def _extract_openai_text(data: dict, agent_name: str) -> str:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"{agent_name}: unexpected response shape: {json.dumps(data)[:300]}") from exc
    content = message.get("content")
    if isinstance(content, list):  # some providers return content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        content = message.get("reasoning_content") or ""
    # Reasoning models may wrap thoughts in <think> tags; strip them.
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()


# ----------------------------------------------------------------------- mock
APPROVAL_WORDS = ("approve", "setuju", "ok", "oke", "ya", "yes", "go", "lanjut", "proceed", "execute", "jalankan")


MOCK_TEXT = {
    "id": {
        "ask": "Terima kasih! (mode mock) Boleh jelaskan format output yang diharapkan dan batasan lainnya? Balas 'ok' kalau sudah siap dibuatkan rencana.",
        "plan": "Siap — saya susun rencananya bersama Agent B dulu, lalu saya tunjukkan ke Anda untuk disetujui.",
        "execute": "Baik, rencana disetujui. Saya serahkan ke Agent A sekarang.",
        "wait": "Dicatat. Ketik 'setuju' untuk mulai eksekusi, atau sebutkan apa yang perlu diubah di rencananya.",
        "objective": "(mock) Menyelesaikan permintaan pengguna.",
        "assumption": "Provider mock; tidak ada penalaran sungguhan di sini.",
        "step1": ("Analisis permintaan dan dokumen", "Rangkum tugas dan bahan masukan.", "Analisis singkat"),
        "step2": ("Susun hasil akhir", "Tulis output final.", "Output final"),
        "criteria": ["Menjawab permintaan", "Memakai dokumen yang diunggah"],
        "review_ok": "Rencana sudah lengkap.",
        "present": "Ini rencana yang sudah saya sepakati dengan Agent B (mock):\n\n1. Analisis permintaan dan dokumen\n2. Susun hasil akhir\n\nBalas **setuju** untuk mulai, atau sebutkan perubahan yang diinginkan.",
        "output": "⚠️ **MODE DEMO (mock)** — {name} tidak benar-benar bekerja. Buka ⚙ **Pengaturan tim** di web, lalu isi provider, model, dan API key untuk agent ini.\n\n_Tugas yang diminta:_ {preview}",
        "verdict": "Memenuhi kriteria penerimaan (mock).",
        "deliver": "Semua langkah selesai dan sudah ditinjau Agent B (mode mock). Hasil gabungan:\n\n",
    },
    "en": {
        "ask": "Thanks! (mock mode) Could you tell me the expected output format and any constraints? Reply 'ok' when you're ready for a plan.",
        "plan": "Got it — I'll draft a plan with Agent B and show it to you for approval.",
        "execute": "Great, plan approved. Handing it to Agent A now.",
        "wait": "Noted. Say 'approve' to start execution or tell me what to change in the plan.",
        "objective": "(mock) Deliver what the user asked for.",
        "assumption": "Mock provider; no real reasoning happens here.",
        "step1": ("Analyse the request and documents", "Summarise the task and inputs.", "Short analysis"),
        "step2": ("Produce the deliverable", "Write the final output.", "Final output"),
        "criteria": ["Addresses the request", "Uses the uploaded documents"],
        "review_ok": "Plan looks complete.",
        "present": "Here is the plan Agent B and I agreed on (mock):\n\n1. Analyse the request and documents\n2. Produce the deliverable\n\nReply **approve** to start, or tell me what to change.",
        "output": "⚠️ **DEMO MODE (mock)** — {name} did no real work. Open ⚙ **Team settings** in the web UI and set this agent's provider, model and API key.\n\n_Requested task:_ {preview}",
        "verdict": "Meets the acceptance criteria (mock).",
        "deliver": "All steps are done and reviewed by Agent B (mock mode). Consolidated result:\n\n",
    },
}


async def mock_complete(cfg, purpose: str, messages: list[dict], lang: str = "en") -> str:
    """Offline stand-in that returns the JSON shape each workflow phase expects."""
    await asyncio.sleep(1.2)  # make the UI animation visible
    t = MOCK_TEXT.get(lang, MOCK_TEXT["en"])
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    tail = last_user[-400:].lower()
    user_turns = sum(1 for m in messages if m["role"] == "user")
    approved = any(w in tail.split() for w in APPROVAL_WORDS)

    if purpose == "c_chat":
        if approved or user_turns >= 2:
            return json.dumps({"reply": t["plan"], "action": "plan"})
        return json.dumps({"reply": t["ask"], "action": "chat"})
    if purpose == "c_approval":
        if approved:
            return json.dumps({"reply": t["execute"], "action": "execute"})
        return json.dumps({"reply": t["wait"], "action": "chat"})
    if purpose == "b_plan":
        steps = [t["step1"], t["step2"]]
        return json.dumps({
            "objective": t["objective"],
            "assumptions": [t["assumption"]],
            "steps": [{"id": i, "title": a, "instructions": b, "deliverable": c} for i, (a, b, c) in enumerate(steps, 1)],
            "acceptance_criteria": t["criteria"],
        })
    if purpose == "c_plan_review":
        return json.dumps({"approved": True, "feedback": t["review_ok"]})
    if purpose == "c_present_plan":
        return t["present"]
    if purpose == "a_execute":
        convo = last_user.split("User request (conversation with ", 1)[-1].split("\n\nFull plan:", 1)[0]
        first_user = next((ln[6:] for ln in convo.split("\n") if ln.startswith("User: ")), "")
        return t["output"].format(name=cfg.name, model=cfg.model, preview=first_user[:200])
    if purpose == "b_review":
        return json.dumps({"verdict": "approve", "score": 8, "feedback": t["verdict"]})
    if purpose == "c_deliver":
        results = last_user.split("Approved results from the workers:", 1)[-1].split("Write the final delivery message", 1)[0]
        return t["deliver"] + results.strip()[:3000]
    return "(mock) " + last_user[:200]
