import os
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any


DEFAULT_ROUTER_PROVIDER_ORDER = ["groq", "lmstudio"]
DEFAULT_AGENT_PROVIDER_ORDER = ["gemini-openai-compat", "lmstudio"]

DEFAULT_MODEL_BY_PROVIDER = {
    "groq": "openai/gpt-oss-120b",
    "gemini-openai-compat": "gemini-2.5-flash-lite",
    "lmstudio": "gpt-oss-20b",
}


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: Optional[str] = None
    model: Optional[str] = None
    extra_headers: Optional[Dict[str, str]] = None
    extra_params: Optional[Dict[str, Any]] = None


@dataclass
class RunConfig:
    run_id: str
    providers: Dict[str, ProviderConfig]
    router_provider_order: List[str]
    agent_provider_order: List[str]
    router_model_default: Optional[str] = None
    agent_model_default: Optional[str] = None
    temperature: float = 0.2
    max_tokens: Optional[int] = None
    use_rag: bool = True
    huddles_mode: str = "dialog"

    def to_public_dict(self) -> Dict[str, Any]:
        provs: Dict[str, Dict[str, Any]] = {}
        for k, v in self.providers.items():
            d = asdict(v)
            ak = d.get("api_key")
            if ak is not None:
                d["api_key"] = "REDACTED"
            provs[k] = d
        return {
            "run_id": self.run_id,
            "router_provider_order": self.router_provider_order,
            "agent_provider_order": self.agent_provider_order,
            "providers": provs,
            "router_model_default": self.router_model_default,
            "agent_model_default": self.agent_model_default,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "use_rag": self.use_rag,
            "huddles_mode": self.huddles_mode,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_public_dict(), indent=2)


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def resolve_providers(model_override: Optional[str] = None) -> Dict[str, ProviderConfig]:
    providers: Dict[str, ProviderConfig] = {}

    providers["groq"] = ProviderConfig(
        name="groq",
        base_url=env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        api_key=env("GROQ_API_KEY"),
        model=model_override or env("GROQ_MODEL", DEFAULT_MODEL_BY_PROVIDER["groq"]),
        extra_headers=None,
        extra_params=None,
    )

    providers["gemini-openai-compat"] = ProviderConfig(
        name="gemini-openai-compat",
        base_url=env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        api_key=env("GEMINI_API_KEY"),
        model=model_override or env("GEMINI_MODEL", DEFAULT_MODEL_BY_PROVIDER["gemini-openai-compat"]),
        extra_headers=None,
        extra_params=None,
    )

    providers["lmstudio"] = ProviderConfig(
        name="lmstudio",
        base_url=env("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key=env("LMSTUDIO_API_KEY", "lm-studio"),
        model=model_override or env("LMSTUDIO_MODEL", DEFAULT_MODEL_BY_PROVIDER["lmstudio"]),
        extra_headers=None,
        extra_params=None,
    )

    return providers


def load_run_config(run_id: str, prompt: str) -> RunConfig:
    router_order_env = env("LATTICE_ROUTER_PROVIDER_ORDER")
    agent_order_env = env("LATTICE_AGENT_PROVIDER_ORDER")
    legacy_order_env = env("LATTICE_PROVIDER_ORDER")

    if router_order_env:
        router_provider_order = [p.strip() for p in router_order_env.split(",") if p.strip()]
    elif legacy_order_env:
        router_provider_order = [p.strip() for p in legacy_order_env.split(",") if p.strip()]
    else:
        router_provider_order = DEFAULT_ROUTER_PROVIDER_ORDER.copy()

    if agent_order_env:
        agent_provider_order = [p.strip() for p in agent_order_env.split(",") if p.strip()]
    elif legacy_order_env:
        agent_provider_order = [p.strip() for p in legacy_order_env.split(",") if p.strip()]
    else:
        agent_provider_order = DEFAULT_AGENT_PROVIDER_ORDER.copy()

    model_override = env("LATTICE_MODEL")

    providers = resolve_providers(model_override=model_override)

    forced = env("LATTICE_PROVIDER")
    if forced and forced.lower() in providers:
        router_provider_order = [forced.lower()]
        agent_provider_order = [forced.lower()]

    forced_router = env("LATTICE_ROUTER_PROVIDER")
    if forced_router and forced_router.lower() in providers:
        router_provider_order = [forced_router.lower()]

    forced_agent = env("LATTICE_AGENT_PROVIDER")
    if forced_agent and forced_agent.lower() in providers:
        agent_provider_order = [forced_agent.lower()]

    router_model_default = env("LATTICE_ROUTER_MODEL", DEFAULT_MODEL_BY_PROVIDER.get(router_provider_order[0], None))
    agent_model_default = env("LATTICE_AGENT_MODEL", DEFAULT_MODEL_BY_PROVIDER.get(agent_provider_order[0], None))

    use_rag = env("LATTICE_USE_RAG", "1").lower() not in ("0", "false", "no")
    huddles_mode = (env("LATTICE_HUDDLES", "dialog") or "dialog").strip().lower()
    if huddles_mode not in ("dialog", "synthesis"):
        huddles_mode = "dialog"

    temperature = float(env("LATTICE_TEMPERATURE", "0.2"))
    max_tokens_env = env("LATTICE_MAX_TOKENS")
    max_tokens = int(max_tokens_env) if max_tokens_env else None

    return RunConfig(
        run_id=run_id,
        providers=providers,
        router_provider_order=router_provider_order,
        agent_provider_order=agent_provider_order,
        router_model_default=router_model_default,
        agent_model_default=agent_model_default,
        temperature=temperature,
        max_tokens=max_tokens,
        use_rag=use_rag,
        huddles_mode=huddles_mode,
    )
