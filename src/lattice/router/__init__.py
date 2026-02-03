"""
Router package for LATTICE orchestration.

This package provides the RouterRunner class and modular components
for managing multi-agent workflows, tool manifests, and orchestration.
"""
from __future__ import annotations

from .tools import ROUTER_SYSTEM_PROMPT, build_tools_manifest

__all__ = ["RouterRunner", "build_tools_manifest", "ROUTER_SYSTEM_PROMPT"]


def __getattr__(name: str):
    if name == "RouterRunner":
        from ..router_main import RouterRunner  # local import to avoid circular deps

        return RouterRunner
    raise AttributeError(name)
