import os
import json
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Any, Union
from .constants import (
    DEFAULT_ROUTER_PROVIDER_ORDER,
    DEFAULT_AGENT_PROVIDER_ORDER,
    DEFAULT_MODEL_BY_PROVIDER,
    DEFAULT_TEMPERATURE,
    DEFAULT_HUDDLE_MODE,
    DEFAULT_EXECUTION_MODE,
    DEFAULT_ROUTER_POLICY,
    DEFAULT_WEB_SEARCH_MODE,
    DEFAULT_ROUTER_MAX_STEPS,
    SUPPORTED_EXECUTION_MODES,
    SUPPORTED_HUDDLE_MODES,
    SUPPORTED_ROUTER_POLICIES,
    SUPPORTED_WEB_SEARCH_MODES
)


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: Optional[str] = None
    model: Optional[str] = None
    extra_headers: Optional[Dict[str, str]] = None
    extra_params: Optional[Dict[str, Any]] = None
    
    def to_public_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        if result.get("api_key"):
            result["api_key"] = "REDACTED"
        return result


@dataclass
class SystemLimits:
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: Optional[int] = None
    router_max_steps: int = DEFAULT_ROUTER_MAX_STEPS
    http_timeout: int = 60
    retry_count: int = 2
    max_retry_delay: int = 8


@dataclass
class RagConfig:
    enabled: bool = True
    min_score: float = 0.15
    max_ingest_files: int = 20
    max_file_size: int = 1024 * 1024
    top_k_default: int = 5
    token_limit: int = 50000
    snippet_length: int = 500


@dataclass
class ExecutionConfig:
    mode: str = DEFAULT_EXECUTION_MODE
    huddle_mode: str = DEFAULT_HUDDLE_MODE
    router_policy: str = DEFAULT_ROUTER_POLICY
    web_search_enabled: bool = False
    web_search_mode: str = DEFAULT_WEB_SEARCH_MODE
    
    def __post_init__(self):
        if self.mode not in SUPPORTED_EXECUTION_MODES:
            self.mode = DEFAULT_EXECUTION_MODE
        if self.huddle_mode not in SUPPORTED_HUDDLE_MODES:
            self.huddle_mode = DEFAULT_HUDDLE_MODE
        if self.router_policy not in SUPPORTED_ROUTER_POLICIES:
            self.router_policy = DEFAULT_ROUTER_POLICY
        if self.web_search_mode not in SUPPORTED_WEB_SEARCH_MODES:
            self.web_search_mode = DEFAULT_WEB_SEARCH_MODE


@dataclass
class RunConfig:
    run_id: str
    providers: Dict[str, ProviderConfig]
    router_provider_order: List[str]
    agent_provider_order: List[str]
    router_model_default: Optional[str] = None
    agent_model_default: Optional[str] = None
    
    limits: SystemLimits = field(default_factory=SystemLimits)
    rag: RagConfig = field(default_factory=RagConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)


    websearch_adapter: Optional[Dict[str, Any]] = None
    @property
    def temperature(self) -> float:
        return self.limits.temperature
    
    @property 
    def max_tokens(self) -> Optional[int]:
        return self.limits.max_tokens
        
    @property
    def use_rag(self) -> bool:
        return self.rag.enabled
        
    @property
    def huddles_mode(self) -> str:
        return self.execution.huddle_mode
        
    @property
    def router_policy(self) -> str:
        return self.execution.router_policy
        
    @property
    def web_search_enabled(self) -> bool:
        return self.execution.web_search_enabled
        
    @property
    def router_max_steps(self) -> int:
        return self.limits.router_max_steps

    def to_public_dict(self) -> Dict[str, Any]:
        providers_public = {
            name: provider.to_public_dict() 
            for name, provider in self.providers.items()
        }
        
        out = {
            "run_id": self.run_id,
            "router_provider_order": self.router_provider_order,
            "agent_provider_order": self.agent_provider_order,
            "providers": providers_public,
            "router_model_default": self.router_model_default,
            "agent_model_default": self.agent_model_default,
            "limits": asdict(self.limits),
            "rag": asdict(self.rag),
            "execution": asdict(self.execution)
        }
        if self.websearch_adapter:

            redact_keys = {"firecrawl_api_key"}
            scrubbed = {k: ("REDACTED" if k in redact_keys and self.websearch_adapter.get(k) else v)
                        for k, v in self.websearch_adapter.items()}
            out["websearch_adapter"] = scrubbed
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_public_dict(), indent=2)


class ConfigurationFactory:
    
    @staticmethod
    def _get_env(key: str, default: Optional[str] = None) -> Optional[str]:
        return os.environ.get(key, default)
    
    @staticmethod
    def _get_env_bool(key: str, default: bool) -> bool:
        value = os.environ.get(key, "").lower()
        if value in ("1", "true", "yes", "on"):
            return True
        elif value in ("0", "false", "no", "off"):
            return False
        return default
        
    @staticmethod
    def _get_env_int(key: str, default: int) -> int:
        try:
            return int(os.environ.get(key, str(default)))
        except (ValueError, TypeError):
            return default
            
    @staticmethod
    def _get_env_float(key: str, default: float) -> float:
        try:
            return float(os.environ.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    @classmethod
    def create_provider_configs(cls) -> Dict[str, ProviderConfig]:
        providers = {}
        
        providers["groq"] = ProviderConfig(
            name="groq",
            base_url=cls._get_env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            api_key=cls._get_env("GROQ_API_KEY"),
            model=cls._get_env("GROQ_MODEL", DEFAULT_MODEL_BY_PROVIDER["groq"])
        )

        providers["gemini-openai-compat"] = ProviderConfig(
            name="gemini-openai-compat",
            base_url=cls._get_env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
            api_key=cls._get_env("GEMINI_API_KEY"),
            model=cls._get_env("GEMINI_MODEL", DEFAULT_MODEL_BY_PROVIDER["gemini-openai-compat"])
        )

        providers["lmstudio"] = ProviderConfig(
            name="lmstudio",
            base_url=cls._get_env("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
            api_key=cls._get_env("LMSTUDIO_API_KEY", "lm-studio"),
            model=cls._get_env("LMSTUDIO_MODEL", DEFAULT_MODEL_BY_PROVIDER["lmstudio"])
        )
        
        return providers
    
    @classmethod
    def create_system_limits(cls) -> SystemLimits:
        return SystemLimits(
            temperature=cls._get_env_float("LATTICE_TEMPERATURE", DEFAULT_TEMPERATURE),
            max_tokens=cls._get_env_int("LATTICE_MAX_TOKENS", 0) or None,
            router_max_steps=cls._get_env_int("LATTICE_ROUTER_MAX_STEPS", DEFAULT_ROUTER_MAX_STEPS)
        )
    
    @classmethod
    def create_rag_config(cls) -> RagConfig:
        return RagConfig(
            enabled=cls._get_env_bool("LATTICE_USE_RAG", True),
            min_score=cls._get_env_float("LATTICE_RAG_MIN_SCORE", 0.15),
            max_ingest_files=cls._get_env_int("LATTICE_RAG_MAX_INGEST", 20)
        )
    
    @classmethod
    def create_execution_config(cls) -> ExecutionConfig:
        web_search_mode = cls._get_env("LATTICE_WEB_SEARCH", DEFAULT_WEB_SEARCH_MODE).lower()
        web_search_enabled = False

        if web_search_mode == "on":
            web_search_enabled = True
        elif web_search_mode == "auto":
            router_order = cls._parse_provider_order("LATTICE_ROUTER_PROVIDER_ORDER", DEFAULT_ROUTER_PROVIDER_ORDER)
            adapter_any = (
                bool(cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_URL")) or
                bool(cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_SEARCH_BASE_URL")) or
                cls._get_env_bool("LATTICE_WEB_SEARCH_ADAPTER_ENABLED", False)
            )
            web_search_enabled = ("groq" in router_order) or adapter_any
            
        return ExecutionConfig(
            mode=cls._get_env("LATTICE_MODE", DEFAULT_EXECUTION_MODE),
            huddle_mode=cls._get_env("LATTICE_HUDDLES", DEFAULT_HUDDLE_MODE),
            router_policy=cls._get_env("LATTICE_ROUTER_POLICY", DEFAULT_ROUTER_POLICY),
            web_search_enabled=web_search_enabled,
            web_search_mode=web_search_mode
        )

    @classmethod
    def create_websearch_adapter_config(cls) -> Optional[Dict[str, Any]]:
        enabled_flag = cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_ENABLED", "").lower()

        legacy_url = cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_URL")
        search_base_url = cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_SEARCH_BASE_URL", legacy_url or "")
        if not enabled_flag and not search_base_url:

            return None
        enabled = enabled_flag in ("1", "true", "on", "yes") or bool(search_base_url)
        if not enabled:
            return None
        out: Dict[str, Any] = {
            "enabled": True,
            "search_type": "searxng",
            "search_base_url": search_base_url.rstrip("/"),
            "default_engines": cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_DEFAULT_ENGINES", ""),
            "language": cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_LANGUAGE", "en"),
            "time_range": cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_TIME_RANGE", "month"),
            "fetch_type": cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_FETCH_TYPE", "trafilatura"),
            "firecrawl_base_url": cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_FIRECRAWL_BASE_URL", "").rstrip("/"),
            "firecrawl_api_key": cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_FIRECRAWL_API_KEY", None),
            "k": cls._get_env_int("LATTICE_WEB_SEARCH_ADAPTER_K", 5),
            "cache_dir": cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_CACHE_DIR", ""),
            "respect_robots_txt": cls._get_env_bool("LATTICE_WEB_SEARCH_ADAPTER_RESPECT_ROBOTS", True),
            "denylist_domains": [d.strip() for d in (cls._get_env("LATTICE_WEB_SEARCH_ADAPTER_DENYLIST_DOMAINS", "").split(",")) if d.strip()],
        }
        return out
    
    @classmethod
    def _parse_provider_order(cls, env_key: str, default: List[str]) -> List[str]:
        env_value = cls._get_env(env_key)
        if env_value:
            return [p.strip() for p in env_value.split(",") if p.strip()]
        
        legacy_value = cls._get_env("LATTICE_PROVIDER_ORDER")
        if legacy_value:
            return [p.strip() for p in legacy_value.split(",") if p.strip()]
            
        return default.copy()
    
    @classmethod
    def _resolve_provider_orders(cls, providers: Dict[str, ProviderConfig]) -> tuple[List[str], List[str]]:
        router_order = cls._parse_provider_order("LATTICE_ROUTER_PROVIDER_ORDER", DEFAULT_ROUTER_PROVIDER_ORDER)
        agent_order = cls._parse_provider_order("LATTICE_AGENT_PROVIDER_ORDER", DEFAULT_AGENT_PROVIDER_ORDER)
        
        forced_provider = cls._get_env("LATTICE_PROVIDER")
        if forced_provider and forced_provider.lower() in providers:
            single = [forced_provider.lower()]
            return single, single

        forced_router = cls._get_env("LATTICE_ROUTER_PROVIDER")
        if forced_router and forced_router.lower() in providers:
            router_order = [forced_router.lower()]

        forced_agent = cls._get_env("LATTICE_AGENT_PROVIDER")
        if forced_agent and forced_agent.lower() in providers:
            agent_order = [forced_agent.lower()]
            
        return router_order, agent_order

    @classmethod
    def create_run_config(cls, run_id: str, prompt: str = "") -> RunConfig:
        providers = cls.create_provider_configs()
        router_order, agent_order = cls._resolve_provider_orders(providers)
        
        model_override = cls._get_env("LATTICE_MODEL")
        if model_override:
            for provider in providers.values():
                provider.model = model_override

        router_model_default = cls._get_env("LATTICE_ROUTER_MODEL")
        if not router_model_default and router_order:
            router_model_default = DEFAULT_MODEL_BY_PROVIDER.get(router_order[0])

        agent_model_default = cls._get_env("LATTICE_AGENT_MODEL")
        if not agent_model_default and agent_order:
            agent_model_default = DEFAULT_MODEL_BY_PROVIDER.get(agent_order[0])
        
        rc = RunConfig(
            run_id=run_id,
            providers=providers,
            router_provider_order=router_order,
            agent_provider_order=agent_order,
            router_model_default=router_model_default,
            agent_model_default=agent_model_default,
            limits=cls.create_system_limits(),
            rag=cls.create_rag_config(),
            execution=cls.create_execution_config()
        )

        rc.websearch_adapter = cls.create_websearch_adapter_config()
        return rc


def load_run_config(run_id: str, prompt: str = "") -> RunConfig:
    return ConfigurationFactory.create_run_config(run_id, prompt)
