from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..config import ConfigurationFactory


def _norm_root(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def codebase_key(codebase_root: str) -> str:
    root = _norm_root(codebase_root or os.getcwd())
    return hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]


@dataclass
class WritePolicySpec:
    allow_globs: Optional[List[str]] = None
    deny_globs: List[str] = field(default_factory=list)


@dataclass
class FeaturesetSpec:
    id: str
    tool_names: List[str]
    prompt_prelude: str = ""
    write_policy: Optional[WritePolicySpec] = None


@dataclass
class ToolboxVariantSpec:
    id: str
    description: str
    tool_names: List[str] = field(default_factory=list)
    featuresets: List[str] = field(default_factory=list)
    prompt_prelude: str = ""
    write_policy: WritePolicySpec = field(default_factory=WritePolicySpec)
    max_tool_iters: int = 8
    tool_choice: str = "auto"
    temperature: Optional[float] = None
    model_overrides: Optional[Dict[str, str]] = None


class AgentRegistry:
    def __init__(self, *, codebase_root: str) -> None:
        self.codebase_root = codebase_root
        self._cfg = ConfigurationFactory.load_user_config()

    def _merge_dynamic_tools(self, base: Any, overlay: Any) -> Dict[str, Any]:
        base_d = base if isinstance(base, dict) else {}
        over_d = overlay if isinstance(overlay, dict) else {}
        out: Dict[str, Any] = dict(base_d)
        out.update(over_d)
        merged = dict(base_d.get("modules") or {}) if isinstance(base_d.get("modules"), dict) else {}
        if isinstance(over_d.get("modules"), dict):
            merged.update(over_d.get("modules") or {})
        out["modules"] = merged
        return out

    def _defaults(self) -> Dict[str, Any]:
        return {
            "featuresets": {
                "code_editing": {
                    "tool_names": ["read_file", "write_file", "delete_file", "rag_search", "run_command", "request_huddle"],
                    "prompt_prelude": "",
                },
                "research": {
                    "tool_names": ["rag_search", "web_search", "request_huddle"],
                    "prompt_prelude": "",
                },
            },
            "toolbox_variants": {
                "toolbox/generalist": {
                    "description": "General-purpose configurable subagent",
                    "featuresets": ["code_editing"],
                    "tool_names": ["web_search"],
                    "prompt_prelude": "",
                    "write_policy": {"allow_globs": None, "deny_globs": []},
                    "max_tool_iters": 8,
                    "tool_choice": "auto",
                }
            },
            "dynamic_tools": {
                "enabled": True,
                "global_dir": os.path.join(os.path.expanduser("~"), ".lattice", "tools"),
                "codebases_dir": os.path.join(os.path.expanduser("~"), ".lattice", "codebases"),
                "modules": {},
            },
        }

    def _agent_library(self) -> Dict[str, Any]:
        cfg = self._cfg if isinstance(self._cfg, dict) else {}
        lib = cfg.get("agent_library")
        return lib if isinstance(lib, dict) else {}

    def _merged_scope(self) -> Dict[str, Any]:
        defaults = self._defaults()
        lib = self._agent_library()
        out: Dict[str, Any] = dict(defaults)
        for key in ("featuresets", "toolbox_variants"):
            if isinstance(lib.get(key), dict):
                merged = dict(out.get(key) if isinstance(out.get(key), dict) else {})
                merged.update(lib.get(key) or {})
                out[key] = merged
        if isinstance(lib.get("dynamic_tools"), dict):
            out["dynamic_tools"] = self._merge_dynamic_tools(out.get("dynamic_tools"), lib.get("dynamic_tools"))
        if isinstance(lib.get("codebases"), dict):
            out["codebases"] = lib.get("codebases")
        cb = (lib.get("codebases") if isinstance(lib.get("codebases"), dict) else {}) or {}
        k = codebase_key(self.codebase_root)
        scoped = cb.get(k)
        if isinstance(scoped, dict):
            for key in ("featuresets", "toolbox_variants", "dynamic_tools"):
                if isinstance(scoped.get(key), dict):
                    if key == "dynamic_tools":
                        out[key] = self._merge_dynamic_tools(out.get(key), scoped.get(key))
                    else:
                        merged = dict(out.get(key) if isinstance(out.get(key), dict) else {})
                        merged.update(scoped.get(key) or {})
                        out[key] = merged
        return out

    def list_library(self) -> Dict[str, Any]:
        merged = self._merged_scope()
        dyn = merged.get("dynamic_tools") if isinstance(merged.get("dynamic_tools"), dict) else {}
        if "enabled" not in dyn:
            dyn = dict(dyn)
            dyn["enabled"] = True
        return {
            "codebase_root": self.codebase_root,
            "codebase_key": codebase_key(self.codebase_root),
            "featuresets": merged.get("featuresets") if isinstance(merged.get("featuresets"), dict) else {},
            "toolbox_variants": merged.get("toolbox_variants") if isinstance(merged.get("toolbox_variants"), dict) else {},
            "dynamic_tools": dyn,
        }

    def resolve_toolbox_variant(self, variant_id: str) -> Optional[ToolboxVariantSpec]:
        merged = self._merged_scope()
        variants = merged.get("toolbox_variants") if isinstance(merged.get("toolbox_variants"), dict) else {}
        raw = variants.get(variant_id)
        if not isinstance(raw, dict):
            return None
        wp_raw = raw.get("write_policy") if isinstance(raw.get("write_policy"), dict) else {}
        wp = WritePolicySpec(
            allow_globs=(wp_raw.get("allow_globs") if wp_raw.get("allow_globs") is not None else None),
            deny_globs=[str(x) for x in (wp_raw.get("deny_globs") or []) if str(x).strip()],
        )
        return ToolboxVariantSpec(
            id=variant_id,
            description=str(raw.get("description") or ""),
            tool_names=[str(x) for x in (raw.get("tool_names") or []) if str(x).strip()],
            featuresets=[str(x) for x in (raw.get("featuresets") or []) if str(x).strip()],
            prompt_prelude=str(raw.get("prompt_prelude") or ""),
            write_policy=wp,
            max_tool_iters=int(raw.get("max_tool_iters") or 8),
            tool_choice=str(raw.get("tool_choice") or "auto"),
            temperature=(float(raw.get("temperature")) if raw.get("temperature") is not None else None),
            model_overrides=_normalize_model_overrides(raw.get("model_overrides")),
        )

    def materialize_toolbox_variant(self, variant_id: str) -> Optional[ToolboxVariantSpec]:
        base = self.resolve_toolbox_variant(variant_id)
        if base is None:
            return None
        merged = self._merged_scope()
        feats = merged.get("featuresets") if isinstance(merged.get("featuresets"), dict) else {}
        tool_names: List[str] = []
        prelude_parts: List[str] = []
        merged_wp = WritePolicySpec(
            allow_globs=(list(base.write_policy.allow_globs) if base.write_policy.allow_globs is not None else None),
            deny_globs=list(base.write_policy.deny_globs or []),
        )
        for fs_id in list(base.featuresets or []):
            fs = feats.get(fs_id)
            if not isinstance(fs, dict):
                continue
            for t in (fs.get("tool_names") or []):
                if isinstance(t, str) and t.strip():
                    tool_names.append(t.strip())
            pp = fs.get("prompt_prelude")
            if isinstance(pp, str) and pp.strip():
                prelude_parts.append(pp.strip())
            fs_wp_raw = fs.get("write_policy") if isinstance(fs.get("write_policy"), dict) else None
            if fs_wp_raw is not None:
                allow_raw = fs_wp_raw.get("allow_globs")
                fs_allow = None if allow_raw is None else [str(x) for x in (allow_raw or []) if str(x).strip()]
                fs_deny = [str(x) for x in (fs_wp_raw.get("deny_globs") or []) if str(x).strip()]
                if merged_wp.allow_globs is None and fs_allow is not None:
                    merged_wp.allow_globs = list(fs_allow)
                merged_wp.deny_globs.extend(fs_deny)
        for t in list(base.tool_names or []):
            if isinstance(t, str) and t.strip():
                tool_names.append(t.strip())
        seen = set()
        uniq_tools = []
        for t in tool_names:
            if t not in seen:
                uniq_tools.append(t)
                seen.add(t)
        prelude = "\n\n".join(prelude_parts + ([base.prompt_prelude.strip()] if base.prompt_prelude.strip() else []))
        merged_wp.deny_globs = [x for x in merged_wp.deny_globs if str(x).strip()]
        dedup_deny: List[str] = []
        seen_d = set()
        for d in merged_wp.deny_globs:
            if d not in seen_d:
                dedup_deny.append(d)
                seen_d.add(d)
        merged_wp.deny_globs = dedup_deny
        return ToolboxVariantSpec(
            id=base.id,
            description=base.description,
            tool_names=uniq_tools,
            featuresets=list(base.featuresets or []),
            prompt_prelude=prelude,
            write_policy=merged_wp,
            max_tool_iters=base.max_tool_iters,
            tool_choice=base.tool_choice,
            temperature=base.temperature,
            model_overrides=base.model_overrides,
        )

    def register_toolbox_variant(self, spec: ToolboxVariantSpec, *, scope: str = "codebase") -> None:
        cfg = ConfigurationFactory.load_user_config()
        if not isinstance(cfg, dict):
            cfg = {}
        lib = cfg.get("agent_library")
        if not isinstance(lib, dict):
            lib = {}
            cfg["agent_library"] = lib

        if scope == "global":
            variants = lib.get("toolbox_variants")
            if not isinstance(variants, dict):
                variants = {}
                lib["toolbox_variants"] = variants
            variants[spec.id] = _toolbox_spec_to_dict(spec)
        else:
            cb = lib.get("codebases")
            if not isinstance(cb, dict):
                cb = {}
                lib["codebases"] = cb
            k = codebase_key(self.codebase_root)
            entry = cb.get(k)
            if not isinstance(entry, dict):
                entry = {"root": self.codebase_root}
                cb[k] = entry
            variants = entry.get("toolbox_variants")
            if not isinstance(variants, dict):
                variants = {}
                entry["toolbox_variants"] = variants
            variants[spec.id] = _toolbox_spec_to_dict(spec)

        ConfigurationFactory.save_user_config(cfg)

    def register_featureset(self, spec: FeaturesetSpec, *, scope: str = "codebase") -> None:
        cfg = ConfigurationFactory.load_user_config()
        if not isinstance(cfg, dict):
            cfg = {}
        lib = cfg.get("agent_library")
        if not isinstance(lib, dict):
            lib = {}
            cfg["agent_library"] = lib

        if scope == "global":
            feats = lib.get("featuresets")
            if not isinstance(feats, dict):
                feats = {}
                lib["featuresets"] = feats
            feats[spec.id] = {"tool_names": list(spec.tool_names or []), "prompt_prelude": spec.prompt_prelude, "write_policy": (asdict(spec.write_policy) if spec.write_policy else None)}
        else:
            cb = lib.get("codebases")
            if not isinstance(cb, dict):
                cb = {}
                lib["codebases"] = cb
            k = codebase_key(self.codebase_root)
            entry = cb.get(k)
            if not isinstance(entry, dict):
                entry = {"root": self.codebase_root}
                cb[k] = entry
            feats = entry.get("featuresets")
            if not isinstance(feats, dict):
                feats = {}
                entry["featuresets"] = feats
            feats[spec.id] = {"tool_names": list(spec.tool_names or []), "prompt_prelude": spec.prompt_prelude, "write_policy": (asdict(spec.write_policy) if spec.write_policy else None)}

        ConfigurationFactory.save_user_config(cfg)

    def register_dynamic_tool(self, *, name: str, code: str, description: str = "", scope: str = "codebase") -> Dict[str, Any]:
        cfg = ConfigurationFactory.load_user_config()
        if not isinstance(cfg, dict):
            cfg = {}
        lib = cfg.get("agent_library")
        if not isinstance(lib, dict):
            lib = {}
            cfg["agent_library"] = lib

        dyn = lib.get("dynamic_tools")
        if not isinstance(dyn, dict):
            dyn = dict(self._defaults().get("dynamic_tools") or {})
            lib["dynamic_tools"] = dyn
        global_dir = dyn.get("global_dir") or os.path.join(os.path.expanduser("~"), ".lattice", "tools")
        codebases_dir = dyn.get("codebases_dir") or os.path.join(os.path.expanduser("~"), ".lattice", "codebases")

        if scope == "global":
            base_dir = os.path.expanduser(str(global_dir))
        else:
            k = codebase_key(self.codebase_root)
            base_dir = os.path.join(os.path.expanduser(str(codebases_dir)), k, "tools")
        os.makedirs(base_dir, exist_ok=True)
        path = os.path.join(base_dir, f"{name}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)

        if scope == "global":
            modules = dyn.get("modules")
            if not isinstance(modules, dict):
                modules = {}
                dyn["modules"] = modules
            modules[name] = {"path": path, "description": description}
        else:
            cb = lib.get("codebases")
            if not isinstance(cb, dict):
                cb = {}
                lib["codebases"] = cb
            k = codebase_key(self.codebase_root)
            entry = cb.get(k)
            if not isinstance(entry, dict):
                entry = {"root": self.codebase_root}
                cb[k] = entry
            scoped_dyn = entry.get("dynamic_tools")
            if not isinstance(scoped_dyn, dict):
                scoped_dyn = {}
                entry["dynamic_tools"] = scoped_dyn
            modules = scoped_dyn.get("modules")
            if not isinstance(modules, dict):
                modules = {}
                scoped_dyn["modules"] = modules
            modules[name] = {"path": path, "description": description}

        ConfigurationFactory.save_user_config(cfg)
        return {"name": name, "path": path, "scope": scope}


def _toolbox_spec_to_dict(spec: ToolboxVariantSpec) -> Dict[str, Any]:
    wp = asdict(spec.write_policy) if spec.write_policy else {"allow_globs": None, "deny_globs": []}
    return {
        "description": spec.description,
        "tool_names": list(spec.tool_names or []),
        "featuresets": list(spec.featuresets or []),
        "prompt_prelude": spec.prompt_prelude,
        "write_policy": {"allow_globs": wp.get("allow_globs"), "deny_globs": list(wp.get("deny_globs") or [])},
        "max_tool_iters": int(spec.max_tool_iters),
        "tool_choice": spec.tool_choice,
        "temperature": spec.temperature,
        "model_overrides": spec.model_overrides,
    }


def _normalize_model_overrides(raw: Any) -> Optional[Dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    out: Dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        out[k.strip()] = v.strip()
    return out or None
