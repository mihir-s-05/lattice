import warnings
from typing import Optional, Dict
from .config_new import (
    ProviderConfig,
    RunConfig,
    ConfigurationFactory,
    load_run_config
)
from .constants import (
    DEFAULT_ROUTER_PROVIDER_ORDER,
    DEFAULT_AGENT_PROVIDER_ORDER,
    DEFAULT_MODEL_BY_PROVIDER
)

def _warn_deprecated():
    warnings.warn(
        "Direct import from config.py is deprecated. Use config_new.py instead.",
        DeprecationWarning,
        stacklevel=3
    )

def env(name: str, default: Optional[str] = None) -> Optional[str]:
    _warn_deprecated()
    return ConfigurationFactory._get_env(name, default)

def resolve_providers(model_override: Optional[str] = None) -> Dict[str, ProviderConfig]:
    _warn_deprecated()
    providers = ConfigurationFactory.create_provider_configs()
    if model_override:
        for provider in providers.values():
            provider.model = model_override
    return providers
__all__ = [
    "ProviderConfig",
    "RunConfig", 
    "load_run_config",
    "env",
    "resolve_providers",
    "DEFAULT_ROUTER_PROVIDER_ORDER",
    "DEFAULT_AGENT_PROVIDER_ORDER", 
    "DEFAULT_MODEL_BY_PROVIDER"
]