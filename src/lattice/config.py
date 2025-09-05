import os
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any


DEFAULT_PROVIDER_ORDER = ["groq", "gemini", "lmstudio"]
DEFAULT_MODEL_BY_PROVIDER = {
    "groq": "openai/gpt-oss-20b",
    "gemini": "gemini-2.5-flash",
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
    provider_order: List[str]
    providers: Dict[str, ProviderConfig]
    temperature: float = 0.2
    max_tokens: Optional[int] = None
    use_rag: bool = True

    def to_public_dict(self) -> Dict[str, Any]:
        provs: Dict[str, Dict[str, Any]] = {}
        for k, v in self.providers.items():
            d = asdict(v)
            ak = d.get("api_key")
            if ak:
                d["api_key"] = (ak[:4] + "…" + ak[-4:]) if len(ak) > 8 else "…"
            provs[k] = d
        return {
            "run_id": self.run_id,
            "provider_order": self.provider_order,
            "providers": provs,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "use_rag": self.use_rag,
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

    providers["gemini"] = ProviderConfig(
        name="gemini",
        base_url=env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        api_key=env("GEMINI_API_KEY"),
        model=model_override or env("GEMINI_MODEL", DEFAULT_MODEL_BY_PROVIDER["gemini"]),
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
    order_env = env("LATTICE_PROVIDER_ORDER")
    if order_env:
        provider_order = [p.strip() for p in order_env.split(",") if p.strip()]
    else:
        provider_order = DEFAULT_PROVIDER_ORDER.copy()

    model_override = env("LATTICE_MODEL")

    providers = resolve_providers(model_override=model_override)

    forced = env("LATTICE_PROVIDER")
    if forced:
        forced = forced.lower()
        if forced in providers:
            provider_order = [forced]

    base_url_override = env("LATTICE_BASE_URL")
    if base_url_override and forced:
        providers[forced].base_url = base_url_override

    use_rag = env("LATTICE_USE_RAG", "1").lower() not in ("0", "false", "no")

    temperature = float(env("LATTICE_TEMPERATURE", "0.2"))
    max_tokens_env = env("LATTICE_MAX_TOKENS")
    max_tokens = int(max_tokens_env) if max_tokens_env else None

    return RunConfig(
        run_id=run_id,
        provider_order=provider_order,
        providers=providers,
        temperature=temperature,
        max_tokens=max_tokens,
        use_rag=use_rag,
    )
