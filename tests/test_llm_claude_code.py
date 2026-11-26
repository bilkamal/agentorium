import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from agentorium.config import settings
from agentorium.llm import LLMClient, LLMError, LLMRateLimited, build_claude_cmd
from agentorium.registry import AgentDef

FAKE = Path(__file__).with_name("fake_claude.py")


def make_client(provider="claude_code", model="haiku", effort=""):
    return LLMClient(AgentDef(id="t", name="T", role="worker", provider=provider, model=model, effort=effort))


class Fake:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        wrapper = tmp_path / "claude"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
        wrapper.chmod(0o755)
        self.old = (settings.claude_bin, settings.claude_timeout, settings.llm_concurrency)
        object.__setattr__(settings, "claude_bin", str(wrapper))
        monkeypatch.setenv("FAKE_CLAUDE_ARGS", str(tmp_path / "args.json"))
        monkeypatch.setenv("FAKE_CLAUDE_STDIN", str(tmp_path / "stdin.txt"))

    def mode(self, m):
        self.monkeypatch.setenv("FAKE_CLAUDE_MODE", m)

    def args(self):
        return json.loads((self.tmp / "args.json").read_text())

    def stdin(self):
        return (self.tmp / "stdin.txt").read_text()

    def restore(self):
        object.__setattr__(settings, "claude_bin", self.old[0])
        object.__setattr__(settings, "claude_timeout", self.old[1])
        object.__setattr__(settings, "llm_concurrency", self.old[2])


@pytest.fixture
def fake_claude(tmp_path, monkeypatch, tmp_settings):
    f = Fake(tmp_path, monkeypatch)
    yield f
    f.restore()


USER = [{"role": "user", "content": "halo"}]


def test_complete_json_returns_structured_output(fake_claude):
    fake_claude.mode("ok")
    c = make_client()
    out = asyncio.run(c.complete_json("sys", USER, {"type": "object"}))
    assert out == {"a": 1}
    assert c.last_usage["input_tokens"] == 5
    args = fake_claude.args()
    assert args[:3] == ["-p", "--output-format", "json"]
    assert args[args.index("--tools") + 1] == ""
    assert "--no-session-persistence" in args and "--strict-mcp-config" in args
    assert args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--system-prompt") + 1] == "sys"
    assert json.loads(args[args.index("--json-schema") + 1]) == {"type": "object"}
    assert fake_claude.stdin() == "halo"


def test_complete_without_schema_returns_result_text(fake_claude):
    fake_claude.mode("text")
    out = asyncio.run(make_client().complete("sys", USER))
    assert out == "halo juga"
    assert "--json-schema" not in fake_claude.args()


def test_effort_flag_only_when_set():
    assert "--effort" not in build_claude_cmd("haiku", "s", None, "")
    cmd = build_claude_cmd("haiku", "s", None, "high")
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert cmd[0] == settings.claude_bin


def test_multi_turn_messages_flattened(fake_claude):
    fake_claude.mode("ok")
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
    asyncio.run(make_client().complete_json("sys", msgs, {"type": "object"}))
    assert fake_claude.stdin() == "User: a\n\nAssistant: b\n\nUser: c"


def test_rate_limit_raises_llm_rate_limited(fake_claude):
    fake_claude.mode("ratelimit")
    with pytest.raises(LLMRateLimited):
        asyncio.run(make_client().complete_json("sys", USER, {"type": "object"}))


def test_login_error_message_mentions_login(fake_claude):
    fake_claude.mode("login")
    with pytest.raises(LLMError, match="claude login"):
        asyncio.run(make_client().complete_json("sys", USER, {"type": "object"}))


def test_other_error_raises_llm_error_not_rate_limited(fake_claude):
    fake_claude.mode("error")
    with pytest.raises(LLMError) as e:
        asyncio.run(make_client().complete_json("sys", USER, {"type": "object"}))
    assert not isinstance(e.value, LLMRateLimited)
    assert "boom" in str(e.value)


def test_invalid_structured_output_fails_closed(fake_claude):
    fake_claude.mode("badjson")
    with pytest.raises(LLMError, match="invalid JSON"):
        asyncio.run(make_client().complete_json("sys", USER, {"type": "object"}))


def test_timeout_kills_process(fake_claude, monkeypatch):
    fake_claude.mode("sleep")
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "10")
    object.__setattr__(settings, "claude_timeout", 1)
    t = time.monotonic()
    with pytest.raises(LLMError, match="timeout"):
        asyncio.run(make_client().complete_json("sys", USER, {"type": "object"}))
    assert time.monotonic() - t < 5


def test_missing_binary(fake_claude):
    object.__setattr__(settings, "claude_bin", "/nonexistent/claude")
    with pytest.raises(LLMError, match="claude CLI not found"):
        asyncio.run(make_client().complete_json("sys", USER, {"type": "object"}))


def test_concurrency_limited_to_setting(fake_claude, monkeypatch):
    fake_claude.mode("sleep")
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "0.6")
    object.__setattr__(settings, "llm_concurrency", 2)

    async def run4():
        c = make_client()
        await asyncio.gather(*[c.complete_json("sys", USER, {"type": "object"}) for _ in range(4)])

    t = time.monotonic()
    asyncio.run(run4())
    assert time.monotonic() - t >= 1.2


def test_registry_treats_claude_code_as_configured():
    a = AgentDef(id="x", name="X", role="worker", provider="claude_code")
    assert a.public()["configured"] is True


def test_config_lists_claude_code(tmp_settings):
    from fastapi.testclient import TestClient

    from agentorium.server import app

    assert "claude_code" in TestClient(app).get("/api/config").json()["providers"]


@pytest.mark.live
def test_live_claude_code_structured():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    out = asyncio.run(make_client().complete_json("Jawab sesuai skema.", [{"role": "user", "content": "Balas ok=true"}], schema))
    assert out["ok"] is True
