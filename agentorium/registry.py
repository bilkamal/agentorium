"""Agent registry: the editable roster of agents (persisted in data/agents.json).

Roles:
  client      talks to the user, agrees the plan with the supervisor, delivers the result (exactly one)
  supervisor  plans the work, assigns steps to workers, reviews every result (exactly one)
  worker      executes plan steps (one or more)
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field

from .config import PROVIDERS, env_agent, settings

ROLES = ("client", "supervisor", "worker")
COLORS = ["#4f6df5", "#1a9f5c", "#d98c00", "#d64545", "#8e44ad", "#16a085", "#e67e22", "#2c82c9", "#c2185b", "#7cb342"]
HAIR = ["#f2c14e", "#6b3fa0", "#2b2b2b", "#3b2a1a", "#a0522d", "#e0e0e0", "#8b0000", "#1f3a93"]


@dataclass
class AgentDef:
    id: str
    name: str
    role: str
    provider: str = "mock"
    model: str = "claude-opus-5-5"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 16000
    effort: str = ""            # Anthropic only
    temperature: float | None = None  # OpenAI-compatible only
    color: str = COLORS[0]
    hair: str = HAIR[0]
    skills: str = ""            # free text shown to the supervisor when assigning steps
    persona: str = ""           # optional extra system-prompt text

    def public(self) -> dict:
        data = asdict(self)
        data["has_key"] = bool(self.api_key)
        data["configured"] = self.provider == "mock" or bool(self.api_key) or bool(self.base_url)
        data.pop("api_key")
        return data

    def fingerprint(self) -> tuple:
        return (self.provider, self.model, self.api_key, self.base_url, self.max_tokens, self.effort, self.temperature)


class RegistryError(ValueError):
    pass


class Registry:
    def __init__(self):
        self._file = settings.data_dir / "agents.json"
        self.agents: list[AgentDef] = []
        self._load()

    # ----------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            raw = json.loads(self._file.read_text())
            self.agents = [AgentDef(**{k: v for k, v in item.items() if k in AgentDef.__dataclass_fields__}) for item in raw]
        except (OSError, ValueError, TypeError):
            self.agents = []
        if not self.agents:
            self.agents = self._seed_from_env()
            self.save()

    def save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(a) for a in self.agents], ensure_ascii=False, indent=1))
        tmp.replace(self._file)

    @staticmethod
    def _seed_from_env() -> list[AgentDef]:
        seed = [("A", "Agent A", "worker", "General-purpose executor"), ("B", "Agent B", "supervisor", ""), ("C", "Agent C", "client", "")]
        agents = []
        for i, (key, name, role, skills) in enumerate(seed):
            provider = (env_agent(key, "PROVIDER", "mock") or "mock").lower()
            temp = env_agent(key, "TEMPERATURE")
            agents.append(AgentDef(
                id=key.lower(), name=name, role=role,
                provider=provider if provider in PROVIDERS else "mock",
                model=env_agent(key, "MODEL", "claude-opus-5-5") or "claude-opus-5-5",
                api_key=env_agent(key, "API_KEY", "") or "",
                base_url=env_agent(key, "BASE_URL", "") or "",
                max_tokens=int(env_agent(key, "MAX_TOKENS", "16000") or 16000),
                effort=env_agent(key, "EFFORT", "") or "",
                temperature=float(temp) if temp else None,
                color=COLORS[i], hair=HAIR[i], skills=skills,
            ))
        return agents

    # ----------------------------------------------------------- queries
    def list(self) -> list[AgentDef]:
        return list(self.agents)

    def get(self, agent_id: str) -> AgentDef | None:
        return next((a for a in self.agents if a.id == agent_id), None)

    def by_role(self, role: str) -> list[AgentDef]:
        return [a for a in self.agents if a.role == role]

    def client(self) -> AgentDef:
        return self._single("client")

    def supervisor(self) -> AgentDef:
        return self._single("supervisor")

    def workers(self) -> list[AgentDef]:
        return self.by_role("worker")

    def _single(self, role: str) -> AgentDef:
        found = self.by_role(role)
        if not found:
            raise RegistryError(f"No agent with role '{role}' — add one in Settings")
        return found[0]

    def public(self) -> list[dict]:
        return [a.public() for a in self.agents]

    # ----------------------------------------------------------- mutations
    def upsert(self, data: dict) -> AgentDef:
        agent_id = str(data.get("id") or "").strip() or uuid.uuid4().hex[:8]
        existing = self.get(agent_id)
        role = str(data.get("role", existing.role if existing else "worker"))
        if role not in ROLES:
            raise RegistryError(f"role must be one of {ROLES}")
        provider = str(data.get("provider", existing.provider if existing else "mock")).lower()
        if provider not in PROVIDERS:
            raise RegistryError(f"provider must be one of {PROVIDERS}")
        if role != "worker" and any(a.role == role and a.id != agent_id for a in self.agents):
            raise RegistryError(f"there can be only one {role}")
        if existing and existing.role != role and existing.role != "worker" and not any(a.role == existing.role and a.id != agent_id for a in self.agents):
            raise RegistryError(f"the team needs a {existing.role}; add another one first")

        idx = len(self.agents)
        agent = existing or AgentDef(id=agent_id, name="", role=role, color=COLORS[idx % len(COLORS)], hair=HAIR[idx % len(HAIR)])
        agent.role = role
        agent.provider = provider
        agent.name = str(data.get("name", agent.name)).strip()[:40] or f"Agent {agent_id}"
        for key in ("model", "base_url", "effort", "skills", "persona", "color", "hair"):
            if key in data and data[key] is not None:
                setattr(agent, key, str(data[key]).strip()[:4000])
        if "api_key" in data and data["api_key"] is not None and str(data["api_key"]).strip() != "":
            agent.api_key = str(data["api_key"]).strip()   # blank keeps the stored key
        if data.get("clear_key"):
            agent.api_key = ""
        if "max_tokens" in data and data["max_tokens"]:
            agent.max_tokens = max(256, int(data["max_tokens"]))
        if "temperature" in data:
            agent.temperature = float(data["temperature"]) if data["temperature"] not in (None, "") else None
        if not existing:
            self.agents.append(agent)
        self.save()
        return agent

    def delete(self, agent_id: str) -> None:
        agent = self.get(agent_id)
        if not agent:
            raise RegistryError("agent not found")
        if agent.role != "worker":
            raise RegistryError(f"the {agent.role} cannot be deleted; change its settings instead")
        if agent.role == "worker" and len(self.workers()) <= 1:
            raise RegistryError("the team needs at least one worker")
        self.agents.remove(agent)
        self.save()


registry = Registry()
