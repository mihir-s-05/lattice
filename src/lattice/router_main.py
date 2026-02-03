from __future__ import annotations

import json
import hashlib
import glob
import os
import random
import shutil
import subprocess
import shlex
import time
from dataclasses import asdict
from urllib.parse import urlparse
import ipaddress
from typing import Any, Dict, List, Optional, Tuple

from .agents import (
    AgentPlan,
    AgentReport,
    ArtifactRef,
    BackendAgent,
    FrontendAgent,
    LLMApiAgent,
    TestAgent,
)
from .artifacts import ArtifactStore
from .config import RunConfig, load_run_config
from .contracts import ContractRunner
from .errors import ProviderError
from .huddle import (
    DecisionSummary,
    parse_decision_summaries,
    save_decisions,
    save_huddle,
    decision_injection_text,
    ensure_unique_ids,
    dedupe_decisions,
    ensure_provenance_links,
    validate_decision_integrity,
)
from .ids import ulid
from .rag import RagIndex
from .runlog import RunLogger
from .stage_gates import GateEvaluator, StageGate
from .transcript import RunningTranscript, generate_run_transcript
from .worker import gen_run_id
from .router_llm import RouterLLM
from .plan import PlanGraph, PlanNode
from .knowledge import KnowledgeBus
from .provenance import evidence_from_artifact_path
from .finalize import run_finalization
from .constants import get_runs_base_dir
from .constants import DEFAULT_HUDDLE_DIR
from .router.tools import build_tools_manifest, ROUTER_SYSTEM_PROMPT
from .checklist import Checklist, default_router_checklist
from .coherence import run_coherence_checks


class RouterRunner:
    def __init__(self, cwd: str, run_id: Optional[str] = None, mode: Optional[str] = None, no_websearch: bool = False) -> None:
        self.source_root = cwd
        self.run_id = run_id or gen_run_id()
        self.run_dir = os.path.join(get_runs_base_dir(), self.run_id)
        os.makedirs(self.run_dir, exist_ok=True)
        self._run_started_at = time.time()
        self._workspace_baseline_at: Optional[float] = None
        self.logger = RunLogger(self.run_dir)
        self.artifacts = ArtifactStore(self.run_dir)
        self.rag = RagIndex(self.run_dir)
        self.workspace_root = os.path.join(self.run_dir, "workspace")
        self._prepare_workspace()
        self.cwd = self.workspace_root
        self.cfg: Optional[RunConfig] = None
        self._goal: Optional[str] = None
        default_mode = os.environ.get("LATTICE_MODE")
        if not mode and not default_mode:
            default_mode = "weave"
        self.mode = (mode or default_mode or "ladder").strip().lower()
        if self.mode not in ("ladder", "tracks", "weave"):
            self.mode = "ladder"

        self._decisions: List[DecisionSummary] = []
        self._provider_usage: Dict[str, int] = {}
        self._max_slice_agents: int = 3
        self._max_open_huddles: int = 2
        self._cooldown_threshold: int = 2
        self._cooldown_seconds: float = 5.0
        self._gate_failures: Dict[str, Tuple[int, float]] = {}
        self._web_recent: List[Dict[str, Any]] = []
        self._web_disabled_by_flag: bool = bool(no_websearch)
        self._saw_schedule_slice: bool = False
        self._saw_write_file: bool = False
        self._saw_run_contract_tests: bool = False
        self._saw_command_test_result: bool = False
        self._saw_agents_spawned: bool = False
        self._latest_gate_results: List[StageGate] = []
        self._touched_files: set[str] = set()
        self._seeded_test_files: set[str] = set()
        self._checklist_created: bool = False
        self._checklist: Checklist = self._load_or_init_checklist()
        self._stage_order: List[str] = []
        self._current_step: Optional[str] = None
        self._checkins_by_stage: Dict[str, Dict[str, Any]] = {}
        self._agent_permissions: Dict[str, Dict[str, Any]] = {
            "backend": {"allow_globs": ["backend/**", "contracts/openapi.yaml"], "deny_globs": []},
            "frontend": {"allow_globs": ["fe/**", "frontend/**"], "deny_globs": []},
            "llmapi": {"allow_globs": ["llm/**"], "deny_globs": []},
            "tests": {"allow_globs": ["tests/**", "contracts/tests/**"], "deny_globs": []},
        }

    def _prepare_workspace(self) -> None:
        os.makedirs(self.workspace_root, exist_ok=True)
        seed = (os.environ.get("LATTICE_WORKSPACE_SEED") or "copy").strip().lower()
        if seed in ("0", "false", "none", "empty", "skip"):
            self.logger.log("workspace_seed", mode=seed, source=self.source_root, dest=self.workspace_root)
            self._workspace_baseline_at = time.time() + 0.25
            self._seeded_test_files = set()
            return
        try:
            if any(os.scandir(self.workspace_root)):
                self.logger.log("workspace_seed", mode="reuse", source=self.source_root, dest=self.workspace_root)
                self._workspace_baseline_at = None
                self._seeded_test_files = set()
                return
        except OSError:
            pass

        ignore_dirs = {
            ".git",
            ".lattice",
            "node_modules",
            "venv",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".cache",
            "dist",
            "build",
            "reports",
            "artifacts",
        }
        root_ignore_names: set[str] = set()
        root_real = os.path.normcase(os.path.realpath(os.path.abspath(self.source_root)))
        try:
            runs_root = os.path.normcase(os.path.realpath(os.path.abspath(get_runs_base_dir())))
            if os.path.commonpath([root_real, runs_root]) == root_real:
                rel = os.path.relpath(runs_root, root_real)
                top = rel.split(os.sep, 1)[0].strip()
                if top and top not in (".", os.curdir):
                    root_ignore_names.add(top)
        except (OSError, ValueError):
            pass
        try:
            gi = os.path.join(self.source_root, ".gitignore")
            if os.path.exists(gi):
                with open(gi, "r", encoding="utf-8", errors="ignore") as f:
                    for raw in f.read().splitlines():
                        s = (raw or "").strip()
                        if not s or s.startswith("#"):
                            continue
                        if s.startswith("!"):
                            continue
                        if not s.startswith("/"):
                            continue
                        if any(ch in s for ch in ("*", "?", "[", "]")):
                            continue
                        name = s.strip("/").strip()
                        if name and ("/" not in name):
                            root_ignore_names.add(name)
        except OSError:
            root_ignore_names = set()

        def _ignore(_dir: str, names: list[str]) -> set[str]:
            out: set[str] = set()
            try:
                if root_ignore_names:
                    d_real = os.path.normcase(os.path.realpath(os.path.abspath(_dir)))
                    if d_real == root_real:
                        for n in names:
                            if n in root_ignore_names:
                                out.add(n)
            except (OSError, ValueError):
                pass
            for n in names:
                if n in ignore_dirs:
                    out.add(n)
                    continue
                if n == ".env":
                    out.add(n)
                    continue
                if n.startswith(".env.") and n != ".env.example":
                    out.add(n)
                    continue
                if n.endswith((".pyc", ".pyo", ".log")):
                    out.add(n)
                    continue
            return out

        try:
            t0 = time.time()
            self.logger.log("workspace_seed_start", mode="copy", source=self.source_root, dest=self.workspace_root)
            shutil.copytree(self.source_root, self.workspace_root, dirs_exist_ok=True, ignore=_ignore)
            dt = int((time.time() - t0) * 1000)
            self.logger.log("workspace_seed", mode="copy", source=self.source_root, dest=self.workspace_root, duration_ms=dt)
            self._workspace_baseline_at = time.time() + 0.25
            self._seeded_test_files = set()
            try:
                for pat in (
                    os.path.join(self.workspace_root, "tests", "test_*.py"),
                    os.path.join(self.workspace_root, "backend", "tests", "test_*.py"),
                ):
                    for fp in glob.glob(pat):
                        try:
                            rel = os.path.relpath(fp, self.workspace_root).replace("\\", "/")
                            self._seeded_test_files.add(rel)
                        except ValueError:
                            continue
            except (OSError, ValueError):
                self._seeded_test_files = set()
        except (OSError, shutil.Error) as e:
            self.logger.log("workspace_seed", mode="copy_failed", source=self.source_root, dest=self.workspace_root, error=str(e))
            self._workspace_baseline_at = time.time() + 0.25
            self._seeded_test_files = set()

    def _huddle_topic(self, goal: str) -> str:
        g = (goal or "").strip()
        if not g:
            return "Align API contract"
        return f"Align API contract for: {g}"

    def _agent_context(self, goal: str, decisions: List[DecisionSummary]) -> Dict[str, Any]:
        huddle_summaries: List[str] = []
        try:
            hud_dir = os.path.join(self.run_dir, DEFAULT_HUDDLE_DIR)
            if os.path.isdir(hud_dir):
                paths = [os.path.join(hud_dir, n) for n in os.listdir(hud_dir) if n.endswith(".summary.md")]
                paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                for p in paths[:3]:
                    try:
                        with open(p, "r", encoding="utf-8", errors="replace") as f:
                            huddle_summaries.append(f.read(3000))
                    except OSError:
                        continue
        except OSError:
            huddle_summaries = []
        return {
            "goal": goal,
            "decisions": decisions,
            "checklist_prompt": self._checklist.prompt_summary(),
            "workspace_files": sorted(list(self._touched_files))[:200],
            "huddle_summaries": huddle_summaries,
        }

    def _parse_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        if not isinstance(text, str) or not text.strip():
            return None
        s = text.strip()
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        try:
            start = s.find("{")
            end = s.rfind("}")
            if start != -1 and end != -1 and end > start:
                frag = s[start : end + 1]
                obj = json.loads(frag)
                return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        return None

    def _apply_plan_updates_from_decisions(self, decisions: List[DecisionSummary]) -> Dict[str, Any]:
        if not decisions:
            return {}
        desired_mode: Optional[str] = None
        desired_stage_order: Optional[List[str]] = None
        for d in decisions:
            meta_mode = (d.meta or {}).get("mode")
            if isinstance(meta_mode, str) and meta_mode.strip().lower() in ("ladder", "tracks", "weave"):
                desired_mode = meta_mode.strip().lower()
            meta_order = (d.meta or {}).get("stage_order")
            if isinstance(meta_order, list) and meta_order:
                vals = [str(x).strip() for x in meta_order if str(x).strip()]
                if vals:
                    desired_stage_order = vals
        if desired_mode is None:
            for d in decisions:
                txt = " ".join([str(d.topic or ""), str(d.decision or "")]).lower()
                if "mode" in txt and "tracks" in txt:
                    desired_mode = "tracks"
                    break
                if "mode" in txt and "ladder" in txt:
                    desired_mode = "ladder"
                    break
        out: Dict[str, Any] = {}
        if desired_mode and desired_mode != self.mode:
            prev = self.mode
            self.mode = desired_mode
            self.logger.log("mode_decision", previous=prev, current=self.mode, reason="huddle_decision")
            out["mode"] = desired_mode
        if desired_stage_order:
            seen = set()
            cleaned: List[str] = []
            for s in desired_stage_order:
                if s in seen:
                    continue
                seen.add(s)
                cleaned.append(s)
            if cleaned:
                self._stage_order = cleaned[:10]
                out["stage_order"] = list(self._stage_order)
        return out

    def _checklist_relpath(self) -> str:
        return os.path.join("artifacts", "checklist.json")

    def _load_or_init_checklist(self) -> Checklist:
        rel = self._checklist_relpath()
        abs_path = os.path.join(self.run_dir, rel)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    chk = Checklist.from_json(f.read())
                    self._checklist_created = True
                    return chk
            except (OSError, ValueError, TypeError):
                return default_router_checklist()
        return default_router_checklist()

    def _save_checklist(self, checklist: Optional[Checklist] = None) -> None:
        chk = checklist or self._checklist
        self.artifacts.add_text("checklist.json", chk.to_json(), tags=["checklist"])
        self._checklist_created = True
        self.logger.log("checklist_saved", path=self._checklist_relpath())

    def _record_touched(self, path: str) -> None:
        try:
            rel = os.path.relpath(path, self.cwd)
            if rel and not rel.startswith(".."):
                self._touched_files.add(os.path.normpath(rel))
        except ValueError:
            return

    def _sync_checklist_state(self) -> None:
        if not self._checklist or not self._checklist.items:
            return
        gate_status = {g.id: g.status for g in (self._latest_gate_results or [])}
        mapping = {
            "sg_api_contract": "contracts",
            "sg_be_scaffold": "backend",
            "sg_fe_scaffold": "frontend",
            "sg_smoke": "tests",
        }
        updates: List[Dict[str, Any]] = []
        for gate_id, item_id in mapping.items():
            if gate_status.get(gate_id) == "passed":
                updates.append({"id": item_id, "status": "done", "note": f"auto: {gate_id} passed"})
        if updates:
            self._checklist.update_items(updates, allow_create=False)

    def _normcase(self, path: str) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    def _is_within_cwd(self, path: str) -> bool:
        root = self._normcase(self.cwd)
        target = self._normcase(path)
        try:
            return os.path.commonpath([root, target]) == root
        except ValueError:
            return target == root or target.startswith(root + os.sep)

    def _resolve_workspace_path(self, path: str) -> str:
        if not path or not str(path).strip():
            raise ValueError("path is required")
        raw = str(path)
        if not os.path.isabs(raw):
            raw = os.path.join(self.cwd, raw)
        abs_path = os.path.realpath(os.path.abspath(raw))
        if not self._is_within_cwd(abs_path):
            raise ValueError("path escapes workspace root")
        return abs_path

    def _ensure_initial_checklist(self, goal: str) -> None:
        if self._checklist_created and self._checklist.items:
            return
        items = [
            {"id": "contracts", "description": "Define API contract + contract tests", "status": "pending", "required": True, "owner": "router"},
            {"id": "backend", "description": "Implement backend scaffold aligned to contract", "status": "pending", "required": True, "owner": "backend", "parent_id": "contracts"},
            {"id": "frontend", "description": "Implement frontend scaffold aligned to contract", "status": "pending", "required": True, "owner": "frontend", "parent_id": "contracts"},
            {"id": "tests", "description": "Run contract/smoke validation", "status": "pending", "required": True, "owner": "tests"},
            {"id": "finalize", "description": "Finalize deliverables + run summary", "status": "pending", "required": False, "owner": "router"},
        ]
        self._checklist.replace_items(items)
        self._checklist_created = True
        self._save_checklist()

    def _command_is_dangerous(self, cmd: str) -> bool:
        text = cmd.lower()
        patterns = [
            r"\brm\s+-rf\s+/",
            r"\brm\s+-fr\s+/",
            r"\brm\s+-r\s+/\b",
            r"\bdel\s+/s\b",
            r"\bformat\b",
            r"\bmkfs\b",
            r"\bdiskpart\b",
            r"\bshutdown\b",
            r"\breboot\b",
            r"\bpoweroff\b",
            r"\bdd\s+if=",
            r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
        ]
        import re
        return any(re.search(p, text) for p in patterns)

    def _command_installs_deps(self, cmd: str) -> bool:
        lowered = cmd.lower()
        markers = [
            "pip install",
            "python -m pip install",
            "pip3 install",
            "pipenv install",
            "poetry install",
            "npm install",
            "pnpm install",
            "yarn install",
        ]
        return any(m in lowered for m in markers)

    def _should_use_shell(self, cmd: str) -> bool:
        text = cmd.strip()
        if not text:
            return True
        meta = ["|", "&", ";", "<", ">", "$", "`"]
        if any(ch in text for ch in meta):
            return True
        head = text.split()[0].lower()
        if os.name == "nt" and head in ("dir", "copy", "type", "del", "ren", "move", "cls", "echo", "set", "cd", "start", "call"):
            return True
        return False

    def _split_command_args(self, cmd: str) -> List[str]:
        if os.name != "nt":
            return shlex.split(cmd)
        parts = shlex.split(cmd, posix=False)
        out: List[str] = []
        for p in parts:
            if len(p) >= 2 and ((p[0] == p[-1] == '"') or (p[0] == p[-1] == "'")):
                out.append(p[1:-1])
            else:
                out.append(p)
        return out

    def _rewrite_leading_cd(self, cmd: str) -> Tuple[Optional[str], str]:
        text = cmd.strip()
        if not text.lower().startswith("cd "):
            return (None, cmd)
        if "&&" not in text and ";" not in text:
            return (None, cmd)
        for sep in ("&&", ";"):
            idx = text.find(sep)
            if idx <= 0:
                continue
            prefix = text[:idx].strip()
            rest = text[idx + len(sep) :].strip()
            if not rest:
                continue
            try:
                parts = self._split_command_args(prefix)
            except ValueError:
                continue
            if len(parts) < 2 or parts[0].lower() != "cd":
                continue
            rel = parts[1]
            try:
                abs_cwd = self._resolve_workspace_path(rel)
            except ValueError:
                continue
            return (abs_cwd, rest)
        return (None, cmd)

    def _validate_command(self, cmd: str) -> Optional[str]:
        if self._command_is_dangerous(cmd):
            return "command blocked: dangerous pattern detected"
        policy = getattr(self.cfg, "command_policy", None) if self.cfg else None
        allowlist = list((policy.allowlist if policy else []) or [])
        denylist = list((policy.denylist if policy else []) or [])

        lowered = cmd.lower()
        blocked_tokens = [
            "curl ",
            "wget ",
            "invoke-webrequest",
            "iwr ",
            "irm ",
            "powershell ",
            "pwsh ",
            "certutil",
            "bitsadmin",
            "ssh ",
            "scp ",
            "sftp ",
            "ftp ",
            "telnet ",
            "nc ",
            "ncat ",
            "netcat ",
            "socat ",
        ]
        if any(t in lowered for t in blocked_tokens):
            return "command blocked: network-capable tooling is disabled by default"
        for d in denylist:
            if d and d.lower() in lowered:
                return f"command blocked by denylist entry: {d}"

        if allowlist:
            try:
                parts = self._split_command_args(cmd)
                head = parts[0] if parts else ""
            except ValueError:
                head = cmd.strip().split(" ")[0]
            if not any(head.lower().startswith(a.lower()) for a in allowlist):
                return "command blocked: not in allowlist"
        else:
            try:
                parts = self._split_command_args(cmd)
                head = (parts[0] if parts else "").lower()
            except ValueError:
                head = (cmd.strip().split(" ")[0] if cmd.strip() else "").lower()
            default_allow = {"python", "py", "pytest", "pip", "pip3", "uv", "poetry", "npm", "node", "npx", "pnpm", "yarn", "git", "rg", "ruff", "black", "mypy", "echo", "dir", "type", "cat"}
            if head and head not in default_allow:
                return f"command blocked: not in default safe allowlist (head={head}). Configure allowlist to permit."
        return None

    def _finalization_allowed(
        self,
        evaluator: Optional[GateEvaluator] = None,
        gates: Optional[List[StageGate]] = None,
    ) -> Tuple[bool, List[str]]:
        missing: List[str] = []
        if not self._checklist_created:
            missing.append("checklist not created; call create_checklist first")
        if not self._checklist.items:
            missing.append("checklist has no items")
        if not self._checklist.is_complete():
            missing.append("checklist incomplete: " + ", ".join(self._checklist.required_incomplete()))
        if evaluator is not None and gates:
            try:
                gres = evaluator.evaluate(gates)
                failed = [g.id for g in gres if g.status != "passed"]
                if failed:
                    missing.append("stage gates not passed: " + ", ".join(failed))
            except Exception as e:
                missing.append(f"stage gates could not be evaluated: {e}")
        return (len(missing) == 0, missing)

    def _finalization_gates(self, gates: List[StageGate]) -> List[StageGate]:
        order = [str(s).strip().lower() for s in (self._stage_order or []) if str(s).strip()]
        want: set[str] = set()
        if "contracts" in order:
            want.add("sg_api_contract")
        if "backend_scaffold" in order:
            want.update({"sg_api_contract", "sg_be_scaffold"})
        if "frontend_scaffold" in order:
            want.add("sg_fe_scaffold")
        if "smoke_tests" in order:
            want.add("sg_smoke")
        if not want:
            return []
        return [g for g in (gates or []) if g.id in want]

    def _shell_exec_args(self, cmd: str) -> List[str]:
        if os.name == "nt":
            ps = shutil.which("pwsh") or shutil.which("powershell")
            if ps:
                return [ps, "-NoProfile", "-NonInteractive", "-Command", cmd]
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            return [comspec, "/c", cmd]
        sh = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
        return [sh, "-lc", cmd]

    def _execute_huddle(
        self,
        topic: str,
        questions: List[str],
        proposed_contract: Optional[str],
        transcript: RunningTranscript,
        agents: Dict[str, Any],
        decisions_so_far: List[DecisionSummary],
        *,
        include_agents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        assert self.cfg is not None
        if include_agents:
            agent_attendees = [str(n) for n in include_agents if str(n) in agents]
        else:
            agent_attendees = [n for n in ("backend", "frontend", "llmapi", "tests") if n in agents]
        attendees = ["router"] + agent_attendees
        hud_id = f"hud_{ulid()}"
        self.logger.log("huddle_request", requester="router", attendees=attendees, topic=topic, questions=questions)
        self.logger.log("huddle_open", id=hud_id, topic=topic, requester="router", attendees=attendees, mode=self.cfg.huddles_mode)
        transcript.add_meeting(topic=topic, attendees=attendees, questions=questions or [])

        rllm = RouterLLM(self.cfg, self.logger, tools=self._build_tools_manifest())
        prov = None
        model = None
        message_events: List[Dict[str, str]] = []
        t0 = time.time()
        if (self.cfg.huddles_mode or "dialog") == "synthesis":
            huddle_sys = (
                "You are facilitating a Huddle. Return 1-3 DecisionSummary JSON objects"
                " with fields: id (optional), topic, options[], decision, rationale, risks[], actions[], contracts[], links[], sources[]."
                " You MAY also include meta:{mode:'ladder'|'tracks'|null, stage_order?:string[], note?:string} for operational changes."
                " Requirements:"
                " - options: array (objects or strings). If objects, include id and description."
                " - sources: array with at least 3 external entries, each {type:'external',url:'<url>',title:'<title>'}."
                " Use web_search tool if available to find relevant sources."
                " Output only JSON (array or one object)."
            )
            lines = [f"Huddle Topic: {topic}"]
            if questions:
                lines.append("Questions:")
                lines += [f"- {q}" for q in questions]
            if proposed_contract:
                lines.append("Proposed contract excerpt:\n" + proposed_contract[:3000])
            huddle_msgs: List[Dict[str, Any]] = [{"role": "system", "content": huddle_sys}, {"role": "user", "content": "\n".join(lines)}]
            huddle_tools = []
            for t in (self._build_tools_manifest() or []):
                name = (t.get("function", {}) or {}).get("name")
                if name in ("web_search", "rag_search"):
                    huddle_tools.append(t)
            out = ""
            for _round in range(4):
                if huddle_tools and self.cfg.web_search_enabled:
                    out_obj = rllm._call_with_tools(huddle_msgs, tools=huddle_tools, phase="huddle", tool_choice="auto")
                else:
                    out_obj = rllm._call(huddle_msgs, phase="huddle")
                prov = out_obj.get("provider")
                model = out_obj.get("model")
                tool_calls = out_obj.get("tool_calls") or []
                if tool_calls:
                    huddle_msgs.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
                    for tc in tool_calls:
                        tname = (tc or {}).get("function", {}).get("name") or ""
                        targs_s = (tc or {}).get("function", {}).get("arguments") or "{}"
                        try:
                            targs = json.loads(targs_s)
                        except json.JSONDecodeError:
                            targs = {}
                        obs: Dict[str, Any] = {}
                        if tname == "web_search":
                            q = targs.get("query") or ""
                            k = int(targs.get("top_k") or 5)
                            tr = targs.get("time_range")
                            eng = targs.get("engines")
                            lang = targs.get("language")
                            pageno = targs.get("pageno")
                            obs = self._web_search_exec(q, k, tr, eng, lang, pageno)
                            entry = {"ts": time.time(), "huddle_id": hud_id, "query": q, "obs": obs}
                            self._web_recent.append(entry)
                            if len(self._web_recent) > 5:
                                self._web_recent = self._web_recent[-5:]
                        elif tname == "rag_search":
                            q = targs.get("query") or ""
                            k = int(targs.get("top_k") or 5)
                            hits = self.rag.search(q, top_k=k)
                            obs = {"hits": hits}
                        else:
                            obs = {"error": "tool_unavailable", "note": f"Unsupported huddle tool: {tname}"}
                        huddle_msgs.append({
                            "role": "tool",
                            "tool_call_id": (tc.get("id") if isinstance(tc, dict) else None),
                            "name": tname,
                            "content": json.dumps(obs, ensure_ascii=False),
                        })
                    continue
                out = out_obj.get("text") or ""
                break
            transcript.add_model_call(title="Huddle", provider=prov or "?", model=model or "?", messages=huddle_msgs[-6:], output=out)
            decisions = parse_decision_summaries(out)
            rec, t_rel, r_rel = save_huddle(
                run_dir=self.run_dir,
                artifacts=self.artifacts,
                rag_index=self.rag,
                requester="router",
                attendees=attendees,
                topic=topic,
                questions=questions or [],
                notes="DecisionSummaries produced by LLM.",
                decisions=decisions,
                hud_id=hud_id,
                mode="synthesis",
                auto_decision=True,
                messages=None,
            )
            try:
                decisions = ensure_unique_ids(decisions)
                decisions = dedupe_decisions(decisions)
                decisions = ensure_provenance_links(decisions, default_link={"title": "Huddle Transcript", "url": os.path.join(self.run_dir, t_rel)})
                validate_decision_integrity(decisions)
            except Exception as e:
                self.logger.log("decision_integrity_error", error=str(e))
            saved = save_decisions(self.run_dir, self.artifacts, self.rag, decisions)
            for d, rel in saved:
                self.logger.log("decision_summary", decision_id=d.id, topic=d.topic, decision=d.decision, path=os.path.join(self.run_dir, rel))
                self.logger.log("huddle_decision", huddle_id=hud_id, decision_summary_id=d.id, synthesis_provider=prov, model=model)
            dt = int((time.time() - t0) * 1000)
            self.logger.log("huddle_close", huddle_id=hud_id, duration_ms=dt, message_count=0)
            self.logger.log(
                "huddle_complete",
                huddle_id=hud_id,
                decisions=[d.id for d in decisions],
                transcript_path=os.path.join(self.run_dir, t_rel),
                router_llm_provider=prov,
                router_llm_model=model,
            )
            return {"huddle_id": hud_id, "decisions": decisions, "transcript_path": t_rel, "summary_path": getattr(rec, "summary_path", None)}
        else:
            from datetime import datetime, timezone
            import re
            def _now() -> str:
                return datetime.now(timezone.utc).isoformat()
            def _summarize_transcript(msgs: List[Dict[str, str]], max_chars: int = 5500) -> str:
                blocks: List[str] = []
                for m in msgs[-20:]:
                    who = m.get('from', '?')
                    content = str(m.get('content', '')).strip()
                    blocks.append(f"{who}:\n{content}")
                text = "\n\n".join(blocks)
                return text if len(text) <= max_chars else text[-max_chars:]
            agree_state: Dict[str, Dict[str, Any]] = {name: {"agree": False, "blockers": []} for name in agent_attendees}
            def _parse_consensus(name: str, content: str) -> None:
                txt = content or ""
                m = re.search(r"(?im)\bAGREE\s*:\s*(yes|no)\b", txt)
                if m:
                    agree_state[name]["agree"] = (m.group(1).lower() == "yes")
                else:
                    if re.search(r"(?i)no\s+blockers|ready\s+to\s+proceed|lgtm|looks\s+good", txt):
                        agree_state[name]["agree"] = True
                blocks = []
                for mm in re.finditer(r"(?im)^\s*(BLOCKERS?|BLOCKING)\s*:\s*(.+)$", txt):
                    blocks.append(mm.group(2).strip())
                if blocks:
                    agree_state[name]["blockers"].extend(blocks)
                if any(b and b.lower() not in ("none", "n/a", "na") for b in agree_state[name]["blockers"]):
                    agree_state[name]["agree"] = False

            router_intro = (
                "Huddle opened. Please reply with: interface deltas, constraints, blocking questions, and a consensus signal.\n"
                "Format: end with `AGREE: yes|no` and optionally `BLOCKERS: …` if no. Keep it concise (<= 8 bullets)."
            )
            self.logger.log("huddle_message", **{"huddle_id": hud_id, "from": "router", "content_preview": router_intro[:500], "content_ref": None})
            message_events.append({"ts": _now(), "from": "router", "content": router_intro})

            inject = decision_injection_text(decisions_so_far) if decisions_so_far else ""
            max_rounds = 5
            round_idx = 0
            while round_idx < max_rounds:
                round_idx += 1
                for name in agent_attendees:
                    agent = agents.get(name)
                    if agent is None:
                        continue
                    goal = (self._goal or "").strip()
                    goal_line = f"Goal: {goal}\n" if goal else "Goal: (not provided)\n"
                    sys_msg = (
                        f"You are the {name.capitalize()}Agent in a huddle for the current goal.\n"
                        + goal_line +
                        "Reply concisely with markdown bullets (<= 8). Include at end: `AGREE: yes|no` and, if no, `BLOCKERS: <brief>`\n"
                        "Focus on interface deltas, constraints, and blocking questions. Ignore unrelated existing repo context unless explicitly relevant to the goal."
                    )
                    user_lines = [
                        f"Agenda: {topic}",
                        "",
                        (f"Goal: {goal}" if goal else "Goal: (not provided)"),
                        "",
                        "Current facts:",
                        inject or "(none)",
                        "",
                        "Transcript so far:",
                        _summarize_transcript(message_events),
                        "",
                        "Open questions:",
                    ] + [f"- {q}" for q in (questions or [])]
                    messages = [
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": "\n".join(user_lines)},
                    ]
                    try:
                        out = agent._model(messages, temperature=0.1)
                    except ProviderError as e:
                        out = f"(error during huddle: {e})"
                    ts = _now()
                    self.logger.log("huddle_message", **{"huddle_id": hud_id, "from": name, "content_preview": (str(out)[:500]), "content_ref": None})
                    message_events.append({"ts": ts, "from": name, "content": str(out)})
                    _parse_consensus(name, str(out))

                all_agree = all(agree_state[n]["agree"] for n in agent_attendees)
                if all_agree:
                    break

                unresolved = [n for n in agent_attendees if not agree_state[n]["agree"]]
                follow_lines = [
                    f"Round {round_idx} summary: awaiting consensus from {', '.join(unresolved)}.",
                    "Please address blockers and confirm readiness with `AGREE: yes|no`.",
                ]
                for n in unresolved:
                    bl = agree_state[n]["blockers"]
                    if bl:
                        follow_lines.append(f"- {n} blockers: " + "; ".join(bl)[:300])
                router_msg = "\n".join(follow_lines)
                self.logger.log("huddle_message", **{"huddle_id": hud_id, "from": "router", "content_preview": router_msg[:500], "content_ref": None})
                message_events.append({"ts": _now(), "from": "router", "content": router_msg})

            transcript_blocks: List[str] = []
            for m in message_events:
                transcript_blocks.append(f"{m['from']} says:\n{m['content']}")
            dialog_context = "\n\n".join(transcript_blocks)

            out_obj = rllm.huddle(topic, questions, f"Transcript follows:\n\n{dialog_context}")
            prov = out_obj.get("provider")
            model = out_obj.get("model")
            out = out_obj.get("text") or ""
            transcript.add_model_call(title="Huddle Synthesis", provider=prov or "?", model=model or "?", messages=[{"role":"system","content":"(router_llm)"}], output=out)

            decisions = parse_decision_summaries(out)
            notes_text = "Dialog concluded with consensus." if all(agree_state[n]["agree"] for n in agent_attendees) else "Dialog concluded. Proceeding with best-effort consensus."
            rec, t_rel, r_rel = save_huddle(
                run_dir=self.run_dir,
                artifacts=self.artifacts,
                rag_index=self.rag,
                requester="router",
                attendees=attendees,
                topic=topic,
                questions=questions or [],
                notes=notes_text,
                decisions=decisions,
                hud_id=hud_id,
                mode="dialog",
                auto_decision=False,
                messages=message_events,
            )
            try:
                rec_abs = os.path.join(self.run_dir, r_rel)
                with open(rec_abs, "r", encoding="utf-8") as f:
                    rec_obj = json.load(f)
                rec_obj["synth_provider"] = prov
                rec_obj["synth_model"] = model
                with open(rec_abs, "w", encoding="utf-8") as f:
                    json.dump(rec_obj, f, indent=2)
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                pass
            for d in decisions:
                d.links = (d.links or []) + [{"title": "Huddle Transcript", "url": os.path.join(self.run_dir, t_rel)}]
            saved = save_decisions(self.run_dir, self.artifacts, self.rag, decisions)
            for d, rel in saved:
                self.logger.log("decision_summary", decision_id=d.id, topic=d.topic, decision=d.decision, path=os.path.join(self.run_dir, rel))
                self.logger.log("huddle_decision", huddle_id=hud_id, decision_summary_id=d.id, synthesis_provider=prov, model=model)
            dt = int((time.time() - t0) * 1000)
            self.logger.log("huddle_close", huddle_id=hud_id, duration_ms=dt, message_count=len(message_events))
            self.logger.log(
                "huddle_complete",
                huddle_id=hud_id,
                decisions=[d.id for d in decisions],
                transcript_path=os.path.join(self.run_dir, t_rel),
                router_llm_provider=prov,
                router_llm_model=model,
            )
            return {"huddle_id": hud_id, "decisions": decisions, "transcript_path": t_rel, "summary_path": getattr(rec, "summary_path", None)}

    def run(self, goal: str) -> Dict[str, Any]:
        self._goal = goal
        self.cfg = load_run_config(self.run_id, goal)
        self._ensure_initial_checklist(goal)

        cfg_public = self.cfg.to_public_dict()
        with open(os.path.join(self.run_dir, "config.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(cfg_public, indent=2))
        self.logger.log("run_start", run_id=self.run_id, run_dir=self.run_dir, mode=self.mode, config=cfg_public)

        if getattr(self.cfg, "router_policy", "llm") == "llm":
            return self._run_agentic(goal)

        if isinstance(goal, str) and ("readme" in goal.lower() or "docs" in goal.lower()) and self.mode in ("ladder", "tracks"):
            self.logger.log("plan_switch", from_mode=self.mode, to_mode="weave", reason_type="scope_change", details="goal mentions README/docs", decisions=[])
            self.mode = "weave"

        from .worker import WorkerRunner

        wr = WorkerRunner(self.cwd, self.run_id)
        wr._pre_ingest_repo_files()

        transcript = RunningTranscript(self.run_id)
        kbus = KnowledgeBus(self.run_dir, self.logger)

        rllm = RouterLLM(self.cfg, self.logger, tools=self._build_tools_manifest())
        fe = FrontendAgent("frontend", self.cfg, self.logger, self.artifacts, self.rag, workspace_root=self.cwd)
        be = BackendAgent("backend", self.cfg, self.logger, self.artifacts, self.rag, workspace_root=self.cwd)
        llm = LLMApiAgent("llmapi", self.cfg, self.logger, self.artifacts, self.rag, workspace_root=self.cwd)
        tst = TestAgent("tests", self.cfg, self.logger, self.artifacts, self.rag, workspace_root=self.cwd)
        agents = {"frontend": fe, "backend": be, "llmapi": llm, "tests": tst}

        decisions: List[DecisionSummary] = []
        plan_graph = PlanGraph()
        if self.mode == "weave":
            plan_graph.mode_by_segment = {"critical": "ladder", "docs": "tracks"}
        else:
            plan_graph.mode_by_segment = {"main": self.mode}
        runner = ContractRunner(self.run_dir, self.logger, workspace_root=self.cwd, run_started_at=self._workspace_baseline_at)
        from .constants import DEFAULT_STAGE_GATES
        gates: List[StageGate] = [
            StageGate(
                id=gate["id"],
                name=gate["name"],
                conditions=gate["conditions"],
            ) for gate in DEFAULT_STAGE_GATES
        ]
        evaluator = GateEvaluator(self.run_dir, self.artifacts, self.logger, workspace_root=self.cwd, run_started_at=self._workspace_baseline_at)

        plan_snapshots: List[Dict[str, Any]] = []

        plan_init = None
        try:
            plan_init = rllm.plan_init(goal)
        except ProviderError as e:
            self.logger.log("router_plan_init_failed", error=str(e))
        if plan_init and isinstance(plan_init.get("text"), str):
            try:
                self.artifacts.add_text(os.path.join("plans", "router_plan.txt"), plan_init["text"], tags=["plan", "router"])
            except OSError:
                pass

        if self.mode == "ladder":
            active = [be, llm, tst]
            ctx = self._agent_context(goal, decisions)
            plans = [a.plan("contracts", ctx) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="contracts", plans=[asdict(p) for p in plans])
            for a in active:
                refs = a.act(ctx)
                for r in refs:
                    self._record_touched(r.path)
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            self._sync_checklist_state()
            self._save_checklist()
            if any(a.needs_huddle(ctx) for a in active):
                hud = self._execute_huddle(
                    topic=self._huddle_topic(goal),
                    questions=["Resource fields?", "Endpoints & DTOs?", "Error model?"],
                    proposed_contract=None,
                    transcript=transcript,
                    agents=agents,
                    decisions_so_far=decisions,
                )
                decisions = hud.get("decisions", [])
                transcript.add_decision_injection(decision_injection_text(decisions))

            results = runner.scan_and_run()
            gate_results = evaluator.evaluate([g for g in gates if g.id == 'sg_api_contract'])
            retries = 0
            while not all(g.status == "passed" for g in gate_results) and retries < 3:
                self.logger.log("router_block", step="contracts", reason="gate_failed", gates=[asdict(g) for g in gate_results])
                try:
                    rllm.refine_step(json.dumps({
                        "step": "contracts",
                        "tests": [asdict(r) for r in results],
                        "gates": [asdict(g) for g in gate_results],
                    }))
                except ProviderError as e:
                    self.logger.log("router_refine_step_failed", step="contracts", error=str(e))
                results = runner.scan_and_run()
                gate_results = evaluator.evaluate([g for g in gates if g.id == 'sg_api_contract'])
                retries += 1

            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "contracts",
                    "gates": [asdict(g) for g in gate_results],
                    "tests": [asdict(r) for r in results],
                }
            )

            active = [be, llm]
            ctx = self._agent_context(goal, decisions)
            plans = [a.plan("backend_scaffold", ctx) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="backend_scaffold", plans=[asdict(p) for p in plans])
            for a in active:
                ctx_phase = dict(ctx)
                ctx_phase["phase"] = "backend_scaffold"
                refs = a.act(ctx_phase)
                for r in refs:
                    self._record_touched(r.path)
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            self._sync_checklist_state()
            self._save_checklist()
            results2 = runner.scan_and_run()
            gate_results2 = evaluator.evaluate([g for g in gates if g.id in ('sg_api_contract','sg_be_scaffold')])
            retries = 0
            while not all(g.status == "passed" for g in gate_results2) and retries < 3:
                self.logger.log("router_block", step="backend_scaffold", reason="gate_failed", gates=[asdict(g) for g in gate_results2])
                results2 = runner.scan_and_run()
                gate_results2 = evaluator.evaluate([g for g in gates if g.id in ('sg_api_contract','sg_be_scaffold')])
                retries += 1
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "backend_scaffold",
                    "gates": [asdict(g) for g in gate_results2],
                    "tests": [asdict(r) for r in (results + results2)],
                }
            )

            active = [fe]
            ctx = self._agent_context(goal, decisions)
            plans = [a.plan("frontend_scaffold", ctx) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="frontend_scaffold", plans=[asdict(p) for p in plans])
            for a in active:
                ctx_phase = dict(ctx)
                ctx_phase["phase"] = "frontend_scaffold"
                refs = a.act(ctx_phase)
                for r in refs:
                    self._record_touched(r.path)
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            self._sync_checklist_state()
            self._save_checklist()
            results3 = runner.scan_and_run()
            gate_results3 = evaluator.evaluate([g for g in gates if g.id in ('sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
            retries = 0
            while not all(g.status == "passed" for g in gate_results3) and retries < 3:
                self.logger.log("router_block", step="frontend_scaffold", reason="gate_failed", gates=[asdict(g) for g in gate_results3])
                results3 = runner.scan_and_run()
                gate_results3 = evaluator.evaluate([g for g in gates if g.id in ('sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
                retries += 1
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "frontend_scaffold",
                    "gates": [asdict(g) for g in gate_results3],
                    "tests": [asdict(r) for r in (results + results2 + results3)],
                }
            )

            active = [tst]
            ctx = self._agent_context(goal, decisions)
            plans = [a.plan("smoke_tests", ctx) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="smoke_tests", plans=[asdict(p) for p in plans])
            for a in active:
                ctx_phase = dict(ctx)
                ctx_phase["phase"] = "smoke_tests"
                refs = a.act(ctx_phase)
                for r in refs:
                    self._record_touched(r.path)
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            self._sync_checklist_state()
            self._save_checklist()
            results4 = runner.scan_and_run()
            gate_results4 = evaluator.evaluate([g for g in gates if g.id in ('sg_smoke','sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
            retries = 0
            while not all(g.status == "passed" for g in gate_results4) and retries < 3:
                self.logger.log("router_block", step="smoke_tests", reason="gate_failed", gates=[asdict(g) for g in gate_results4])
                results4 = runner.scan_and_run()
                gate_results4 = evaluator.evaluate([g for g in gates if g.id in ('sg_smoke','sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
                retries += 1
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "smoke_tests",
                    "gates": [asdict(g) for g in gate_results4],
                    "tests": [asdict(r) for r in (results + results2 + results3 + results4)],
                }
            )

        elif self.mode == "tracks":
            slice_active = [fe, be, llm, tst]
            ctx = self._agent_context(goal, decisions)
            plans = [a.plan("tracks", ctx) for a in slice_active]
            self.logger.log("router_plans", mode=self.mode, step="slice-1", plans=[asdict(p) for p in plans])
            for a in slice_active:
                refs = a.act(ctx)
                for r in refs:
                    self._record_touched(r.path)
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            self._sync_checklist_state()
            self._save_checklist()
            results = runner.scan_and_run()
            if any(a.needs_huddle(ctx) for a in slice_active):
                hud = self._execute_huddle(
                    topic=self._huddle_topic(goal),
                    questions=["Resource fields?", "Endpoints & DTOs?", "Error model?"],
                    proposed_contract=None,
                    transcript=transcript,
                    agents=agents,
                    decisions_so_far=decisions,
                )
                decisions = hud.get("decisions", [])
                transcript.add_decision_injection(decision_injection_text(decisions))
            gate_results = evaluator.evaluate(gates)
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "sync-1",
                    "gates": [asdict(g) for g in gate_results],
                    "tests": [asdict(r) for r in results],
                }
            )
        else:
            plan_graph.add_node(PlanNode(id="n_contracts", name="API contracts", modeSegment="critical"))
            plan_graph.add_node(PlanNode(id="n_backend", name="Backend scaffold", modeSegment="critical"))
            plan_graph.add_node(PlanNode(id="n_smoke", name="Smoke tests", modeSegment="critical"))
            plan_graph.add_edge("n_contracts", "n_backend")
            plan_graph.add_edge("n_backend", "n_smoke")

            plan_graph.add_node(PlanNode(id="n_docs", name="Docs/README", modeSegment="docs"))

            active_crit = [be, llm, tst]
            ctx = self._agent_context(goal, decisions)
            plans = [a.plan("contracts", ctx) for a in active_crit]
            self.logger.log("router_plans", mode=self.mode, step="contracts", plans=[asdict(p) for p in plans])
            for a in active_crit:
                refs = a.act(ctx)
                for r in refs:
                    self._record_touched(r.path)
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            self._sync_checklist_state()
            self._save_checklist()
            try:
                doc_out = llm._model([
                    {"role": "system", "content": "You are the Docs agent. Write a concise README for the generated CLI app."},
                    {"role": "user", "content": f"Goal: {goal}\n\nWrite a minimal README with: Overview, Quickstart, Commands, and Notes."},
                ])
            except ProviderError as e:
                self.logger.log("docs_readme_failed", error=str(e))
            else:
                try:
                    readme_art = self.artifacts.add_text("README.md", doc_out, tags=["docs", "readme"], meta={"segment": "docs"})
                except OSError as e:
                    self.logger.log("docs_readme_write_failed", error=str(e))
                else:
                    plan_graph.nodes[-1].evidence.append({"type": "artifact", "id": readme_art.path, "hash": f"sha256:{readme_art.sha256}"})
                    self.logger.log("agent_turn", agent="docs", artifacts=[readme_art.path])

            results = runner.scan_and_run()
            if any(a.needs_huddle(ctx) for a in active_crit):
                hud = self._execute_huddle(
                    topic=self._huddle_topic(goal),
                    questions=["Resource fields?", "Endpoints & DTOs?", "Error model?"],
                    proposed_contract=None,
                    transcript=transcript,
                    agents=agents,
                    decisions_so_far=decisions,
                )
                decisions = hud.get("decisions", [])
                transcript.add_decision_injection(decision_injection_text(decisions))
            gate_results = evaluator.evaluate([g for g in gates if g.id == 'sg_api_contract'])

            sim_path = os.path.join(self.run_dir, "artifacts", "knowledge", "sim_update.json")
            openapi_rel = os.path.join("artifacts", "contracts", "openapi.yaml")
            openapi_abs = os.path.join(self.run_dir, openapi_rel)
            if os.path.exists(openapi_abs) and (not os.path.exists(sim_path)):
                try:
                    os.makedirs(os.path.dirname(sim_path), exist_ok=True)
                    with open(openapi_abs, "rb") as f:
                        openapi_hash = hashlib.sha256(f.read()).hexdigest()
                    with open(sim_path, "w", encoding="utf-8") as f:
                        json.dump(
                            {
                                "source": "artifact",
                                "refs": [{"type": "artifact", "id": openapi_rel, "hash": f"sha256:{openapi_hash}"}],
                            },
                            f,
                            indent=2,
                        )
                except OSError as e:
                    self.logger.log("knowledge_sim_update_write_failed", error=str(e))

            try:
                new_events = kbus.ingest_local_dropins()
            except OSError as e:
                self.logger.log("knowledge_dropins_read_failed", error=str(e))
                new_events = []

            if new_events:
                plan_graph.add_reason("knowledge_update", f"{len(new_events)} new knowledge signal(s)")
                self.logger.log("plan_switch", from_mode=self.mode, to_mode="weave", reason_type="knowledge_update", details=f"{len(new_events)} knowledge events", decisions=[d.id for d in decisions])
                hud = self._execute_huddle(
                    topic="Replan due to knowledge update",
                    questions=["Do we need to adjust contracts or scaffolds?", "Any new risks from the evidence?"],
                    proposed_contract=None,
                    transcript=transcript,
                    agents=agents,
                    decisions_so_far=decisions,
                )
                new_ds = hud.get("decisions", [])
                refs: List[Dict[str, Any]] = []
                for ev in new_events:
                    refs.extend(getattr(ev, "refs", []) or [])
                for dsum in new_ds:
                    if not getattr(dsum, "sources", None):
                        dsum.sources = refs[:]
                decisions = decisions + new_ds
                transcript.add_decision_injection(decision_injection_text(decisions))

            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "contracts/weave_docs",
                    "gates": [asdict(g) for g in gate_results],
                    "tests": [asdict(r) for r in results],
                }
            )

            active = [be, llm]
            ctx = self._agent_context(goal, decisions)
            plans2 = [a.plan("backend_scaffold", ctx) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="backend_scaffold", plans=[asdict(p) for p in plans2])
            for a in active:
                ctx_phase = dict(ctx)
                ctx_phase["phase"] = "backend_scaffold"
                refs = a.act(ctx_phase)
                for r in refs:
                    self._record_touched(r.path)
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            self._sync_checklist_state()
            self._save_checklist()
            results2 = runner.scan_and_run()
            gate_results2 = evaluator.evaluate([g for g in gates if g.id in ('sg_api_contract','sg_be_scaffold')])
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "backend_scaffold",
                    "gates": [asdict(g) for g in gate_results2],
                    "tests": [asdict(r) for r in (results + results2)],
                }
            )

            active_fe = [fe]
            ctx = self._agent_context(goal, decisions)
            plans3 = [a.plan("frontend_scaffold", ctx) for a in active_fe]
            self.logger.log("router_plans", mode=self.mode, step="frontend_scaffold", plans=[asdict(p) for p in plans3])
            for a in active_fe:
                ctx_phase = dict(ctx)
                ctx_phase["phase"] = "frontend_scaffold"
                refs = a.act(ctx_phase)
                for r in refs:
                    self._record_touched(r.path)
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            self._sync_checklist_state()
            self._save_checklist()
            results3 = runner.scan_and_run()
            gate_results3 = evaluator.evaluate([g for g in gates if g.id in ('sg_fe_scaffold',)])

            results4 = runner.scan_and_run()
            gate_results4 = evaluator.evaluate([g for g in gates if g.id in ('sg_smoke','sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "smoke_tests",
                    "gates": [asdict(g) for g in gate_results4],
                    "tests": [asdict(r) for r in (results + results2 + results3 + results4)],
                }
            )

        try:
            plan_graph.save(self.run_dir)
        except OSError as e:
            self.logger.log("plan_graph_save_failed", error=str(e))

        try:
            self.artifacts.add_text(os.path.join("plans", "snapshot.json"), json.dumps(plan_snapshots, indent=2), tags=["plan", "snapshot"])
        except OSError as e:
            self.logger.log("plan_snapshot_write_failed", error=str(e))

        try:
            pre_results = runner.scan_and_run()
            pre_gate_results = evaluator.evaluate(gates)
            self._latest_gate_results = pre_gate_results
            self.logger.log("pre_finalization_validation", tests=[asdict(r) for r in pre_results], gates=[asdict(g) for g in pre_gate_results])
        except Exception as e:
            self.logger.log("pre_finalization_error", error=str(e))

        allowed, missing = self._finalization_allowed(evaluator, gates)
        if allowed:
            final_report = run_finalization(self.run_dir, self.artifacts, self.logger, decisions, evaluator, workspace_root=self.cwd, run_started_at=self._workspace_baseline_at)
        else:
            final_report = {"error": "finalization_blocked", "missing": missing}
            self.logger.log("finalization_blocked", reason="guardrails_policy", missing=missing)

        summary = self._build_summary(agents, evaluator, decisions)
        final_report_rel = os.path.join("artifacts", "finalization", "report.json")
        if os.path.exists(os.path.join(self.run_dir, final_report_rel)):
            summary["finalization_report"] = final_report_rel
        else:
            summary["finalization_report"] = None
            summary["finalization_blocked"] = True
            summary["finalization_missing"] = missing
        self.artifacts.add_text("run_summary.json", json.dumps(summary, indent=2), tags=["summary"])
        self.logger.log("run_complete", summary_path=os.path.join(self.run_dir, "artifacts", "run_summary.json"))
        tp = None
        try:
            tp = generate_run_transcript(self.run_dir)
            self.logger.log("transcript_generated", path=tp)
        except OSError as e:
            self.logger.log("transcript_error", error=str(e))

        return {
            "artifact_dir": os.path.join(self.run_dir, "artifacts"),
            "log_path": self.logger.path(),
            "run_id": self.run_id,
            "workspace_dir": self.workspace_root,
            "summary_path": os.path.join(self.run_dir, "artifacts", "run_summary.json"),
            "transcript_path": tp or os.path.join(self.run_dir, "transcript.md"),
        }

    def _build_tools_manifest(self) -> List[Dict[str, Any]]:
        """Build the tools manifest using the extracted module."""
        return build_tools_manifest()

    def _router_system_prompt(self) -> str:
        """Get the system prompt for the Router LLM."""
        return ROUTER_SYSTEM_PROMPT

    def _snapshot_state(
        self,
        plan_graph: PlanGraph,
        evaluator: GateEvaluator,
        decisions: List[DecisionSummary],
        unread_huddles: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            evaluator.load_test_results()
        except (OSError, ValueError):
            pass
        tool_manifest = [(t.get("function", {}) or {}).get("name") for t in tools]
        from .constants import DEFAULT_STAGE_GATES
        active_gates = [{"id": g["id"], "name": g["name"]} for g in DEFAULT_STAGE_GATES]
        return {
            "plan_graph": plan_graph.snapshot(),
            "mode": self.mode,
            "stage_order": list(self._stage_order or []),
            "current_step": self._current_step,
            "latest_tests": evaluator.latest_tests,
            "active_gates": active_gates,
            "checklist": [i.to_dict() for i in self._checklist.items],
            "unread_huddles": unread_huddles,
            "recent_decisions": [asdict(d) for d in decisions[-5:]],
            "tools": tool_manifest,
        }

    def _web_search_exec(self, query: str, top_k: int, time_range: Optional[str], engines: Optional[str], language: Optional[str], pageno: Optional[int]) -> Dict[str, Any]:
        assert self.cfg is not None
        if self._web_disabled_by_flag:
            self.logger.log(
                "web_search_unavailable",
                provider="lmstudio",
                router_mode="local",
                config={"adapter_enabled": False, "mcp": False},
                reason="disabled_by_flag",
            )
            self.logger.log(
                "web_search",
                source="unavailable",
                query=query,
                params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                results_count=0,
                urls_fetched=0,
                latency_ms=None,
                error="disabled_by_flag",
            )
            return {"error": "tool_unavailable", "reason": "web_search disabled (disabled_by_flag)"}
        if not getattr(self.cfg, "web_search_enabled", False):
            self.logger.log(
                "web_search_unavailable",
                provider="lmstudio",
                router_mode="local",
                config={"adapter_enabled": False, "mcp": False},
                reason="disabled_by_config",
            )
            self.logger.log(
                "web_search",
                source="unavailable",
                query=query,
                params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                results_count=0,
                urls_fetched=0,
                latency_ms=None,
                error="tool_unavailable",
            )
            return {"error": "tool_unavailable", "reason": "disabled_by_config"}


        from datetime import datetime, timezone
        def _now_iso() -> str:
            return datetime.now(timezone.utc).isoformat()


        def _map_time_range(tr: Optional[str]) -> Optional[str]:
            if not tr:
                return None
            m = {"d": "day", "w": "week", "m": "month", "y": "year"}
            return m.get(tr, None)


        router_primary = (self.cfg.router_provider_order[0] if self.cfg.router_provider_order else None) or None
        router_model = (self.cfg.router_model_default or "").strip()
        groq_eligible_models = {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
        wants_groq = (router_primary == "groq") and (router_model in groq_eligible_models)


        adapter_cfg = getattr(self.cfg, "websearch_adapter", None) or {}
        adapter_enabled = bool(adapter_cfg.get("enabled")) and bool(adapter_cfg.get("search_base_url"))


        if wants_groq:
            import time as _t
            t0 = _t.time()
            try:
                rllm = RouterLLM(self.cfg, self.logger, tools=self._build_tools_manifest())
                sys = (
                    "You are a web research assistant. Use the browser_search tool to find relevant sources,"
                    " then return STRICT JSON with keys: query, source, results[], extracts[]."
                    " Shape: {\"query\":str, \"source\":\"groq\", \"results\":[{\"title\":str,\"url\":str,\"snippet\":str,\"engine\":str,\"time?\":str}], \"extracts\":[{\"url\":str,\"content_md\":str,\"status\":200,\"fetched_at\":str}]}."
                    " Only output JSON."
                )
                msg = (
                    f"Query: {query}\nTopK: {top_k}\nTimeRange: {time_range or '-'}\n"
                    f"Engines: {engines or '-'}\nLanguage: {language or '-'}\nPageNo: {pageno or 1}"
                )
                messages = [{"role": "system", "content": sys}, {"role": "user", "content": msg}]
                raw_obj = rllm._call_with_tools(messages, tools=[{"type": "browser_search"}], phase="web_search", tool_choice="required")
                dt_ms = int((_t.time() - t0) * 1000)
                text = (raw_obj.get("text") or "").strip()
                obs: Dict[str, Any]
                try:
                    obs = json.loads(text)

                    obs["query"] = query
                    obs["source"] = "groq"
                    obs.setdefault("results", [])
                    obs.setdefault("extracts", [])
                except json.JSONDecodeError:
                    obs = {
                        "query": query,
                        "source": "groq",
                        "results": [],
                        "extracts": [
                            {"url": "", "content_md": text, "status": 200, "fetched_at": _now_iso()}
                        ],
                    }
                obs["note"] = "source: groq"
                self.logger.log(
                    "web_search",
                    source="groq",
                    query=query,
                    params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                    results_count=len(obs.get("results", [])),
                    urls_fetched=len(obs.get("extracts", [])),
                    latency_ms={"llm_call_ms": dt_ms},
                )
                return obs
            except ProviderError as e:
                self.logger.log("web_search_error", source="groq", query=query, error=str(e))
                if adapter_enabled:
                    self.logger.log("provider_switch", from_provider="groq", to_provider="adapter", reason="groq_search_failed")
                else:
                    self.logger.log(
                        "web_search",
                        source="unavailable",
                        query=query,
                        params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                        results_count=0,
                        urls_fetched=0,
                        latency_ms=None,
                        error=str(e),
                    )
                    return {"error": "tool_unavailable", "reason": f"web_search error: {e}"}


        if not adapter_enabled:
            self.logger.log(
                "web_search_unavailable",
                provider="lmstudio",
                router_mode="local",
                config={"adapter_enabled": False, "mcp": False},
            )
            self.logger.log(
                "web_search",
                source="unavailable",
                query=query,
                params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                results_count=0,
                urls_fetched=0,
                latency_ms=None,
                error="adapter_not_enabled",
            )
            return {"error": "tool_unavailable", "reason": "adapter_not_enabled"}

        import time as _t
        import hashlib
        import requests
        from requests import RequestException
        t_search = _t.time()
        search_base = (adapter_cfg.get("search_base_url") or "").rstrip("/")
        searx_params = {
            "format": "json",
            "q": query,
            "language": (language or adapter_cfg.get("language") or "en"),
        }
        if engines:
            searx_params["engines"] = engines
        elif adapter_cfg.get("default_engines"):
            searx_params["engines"] = adapter_cfg.get("default_engines")
        tr_full = _map_time_range(time_range) or adapter_cfg.get("time_range") or None
        if tr_full:
            searx_params["time_range"] = tr_full
        if pageno and pageno >= 1:
            searx_params["pageno"] = int(pageno)
        try:
            resp = requests.get(f"{search_base}/search", params=searx_params, timeout=30)
            data = resp.json() if resp.ok else {"results": []}
        except (RequestException, ValueError):
            data = {"results": []}
        search_ms = int((_t.time() - t_search) * 1000)
        raw_results = data.get("results", []) or []

        results: List[Dict[str, Any]] = []
        for r in raw_results[: max(1, min(int(top_k or 5), 10))]:
            if not isinstance(r, dict):
                continue
            results.append(
                {
                    "title": r.get("title") or "",
                    "url": r.get("url") or r.get("link") or "",
                    "snippet": (r.get("content") or r.get("summary") or "")[:500],
                    "engine": r.get("engine") or r.get("source") or "",
                    "time?": r.get("publishedDate") or r.get("published_time") or None,
                }
            )


        fetch_type = (adapter_cfg.get("fetch_type") or "trafilatura").lower()
        deny = set([d.lower() for d in (adapter_cfg.get("denylist_domains") or [])])
        max_k = int(adapter_cfg.get("k") or 5)

        def _url_allowed(u: str) -> bool:
            p = urlparse(u)
            if (p.scheme or "").lower() not in ("http", "https"):
                return False
            host = (p.hostname or "").strip().lower()
            if not host:
                return False
            if host in ("localhost",):
                return False
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                    return False
            except ValueError:
                pass
            if host.endswith(".local") or host.endswith(".internal"):
                return False
            return True

        urls = []
        for it in results:
            u = (it.get("url") or "").strip()
            if not u:
                continue
            if not _url_allowed(u):
                continue
            host = u.split("//", 1)[-1].split("/", 1)[0].lower()
            if host and any(host.endswith(d) or host == d for d in deny):
                continue
            urls.append(u)
            if len(urls) >= max_k:
                break

        cache_dir = adapter_cfg.get("cache_dir") or os.path.join(self.run_dir, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        extracts: List[Dict[str, Any]] = []
        t_fetch_total = _t.time()
        for u in urls:
            url_hash = hashlib.sha256(u.encode("utf-8")).hexdigest()
            cache_path = os.path.join(cache_dir, f"{url_hash}.md")
            cached = False
            content_md = ""
            status = 0
            start = _t.time()
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                        content_md = f.read()
                    cached = True
                    status = 200
                except OSError:
                    cached = False
            if not cached:
                if fetch_type == "firecrawl" and adapter_cfg.get("firecrawl_base_url"):
                    fc_base = adapter_cfg.get("firecrawl_base_url").rstrip("/")
                    url_fc = f"{fc_base}/scrape" if fc_base.endswith("/v1") else f"{fc_base}/v1/scrape"
                    headers = {"Content-Type": "application/json"}
                    if adapter_cfg.get("firecrawl_api_key"):
                        headers["Authorization"] = f"Bearer {adapter_cfg['firecrawl_api_key']}"
                    try:
                        r = requests.post(url_fc, headers=headers, json={"url": u, "formats": ["markdown", "html"]}, timeout=45)
                        jd = r.json() if r.ok else {}
                        content_md = jd.get("markdown") or (jd.get("data", {}) or {}).get("markdown") or ""
                        status = r.status_code
                    except (RequestException, ValueError):
                        content_md = ""
                        status = 502
                else:
                    try:
                        import trafilatura

                        downloaded = trafilatura.fetch_url(u)
                        if downloaded is None:
                            status = 404
                            content_md = ""
                        else:
                            extracted = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
                            content_md = extracted or ""
                            status = 200 if content_md else 204
                    except (ImportError, OSError, ValueError):
                        content_md = ""
                        status = 503

                try:
                    if content_md:
                        with open(cache_path, "w", encoding="utf-8") as f:
                            f.write(content_md)
                except OSError:
                    pass
            dur_ms = int((_t.time() - start) * 1000)
            self.logger.log(
                "adapter_fetch",
                url=u,
                fetcher=("firecrawl" if (fetch_type == "firecrawl" and adapter_cfg.get("firecrawl_base_url")) else "trafilatura"),
                status=status,
                bytes=len(content_md.encode("utf-8")) if content_md else 0,
                cached=cached,
                duration_ms=dur_ms,
            )
            extracts.append({"url": u, "content_md": content_md, "status": status, "fetched_at": _now_iso()})
        fetch_ms = int((_t.time() - t_fetch_total) * 1000)

        obs = {
            "query": query,
            "source": "adapter",
            "results": results,
            "extracts": extracts,
            "note": "source: adapter",
        }
        self.logger.log(
            "web_search",
            source="adapter",
            query=query,
            params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
            results_count=len(results),
            urls_fetched=len(extracts),
            latency_ms={"searxng_ms": search_ms, "fetch_total_ms": fetch_ms},
        )
        return obs

    def _run_agentic(self, goal: str) -> Dict[str, Any]:
        self._ensure_initial_checklist(goal)
        from .worker import WorkerRunner
        WorkerRunner(self.cwd, self.run_id)._pre_ingest_repo_files()

        transcript = RunningTranscript(self.run_id)
        kbus = KnowledgeBus(self.run_dir, self.logger)
        rllm = RouterLLM(self.cfg, self.logger, tools=self._build_tools_manifest())

        plan_graph = PlanGraph()
        plan_graph.mode_by_segment = {"main": self.mode}

        stage_order: List[str] = ["contracts", "backend_scaffold", "frontend_scaffold", "smoke_tests"]
        try:
            init = rllm.plan_init(goal)
        except ProviderError as e:
            self.logger.log("router_plan_init_failed", error=str(e))
            init = None
        plan_txt = (init or {}).get("text") or ""
        plan_obj = self._parse_json_object(plan_txt)
        if isinstance(plan_obj, dict):
            rec_mode = str(plan_obj.get("mode") or "").strip().lower()
            if rec_mode in ("ladder", "tracks"):
                prev = self.mode
                self.mode = rec_mode
                plan_graph.mode_by_segment = {"main": self.mode}
                self.logger.log("mode_decision", previous=prev, current=self.mode, reason=str(plan_obj.get("mode_reason") or "plan_init"))
            stages = plan_obj.get("stages")
            if isinstance(stages, list) and stages:
                ids = []
                for st in stages:
                    if isinstance(st, dict) and st.get("id"):
                        sid = str(st.get("id")).strip()
                        if sid:
                            ids.append(sid)
                        chk = st.get("checkin")
                        if isinstance(chk, dict) and sid:
                            when = str(chk.get("when") or "").strip().lower()
                            if when in ("after", "on_blocked", "both"):
                                self._checkins_by_stage[sid] = {"when": when, "topic": chk.get("topic") or ""}
                if ids:
                    stage_order = ids
            try:
                self.artifacts.add_text(os.path.join("plans", "router_plan.json"), json.dumps(plan_obj, indent=2), tags=["plan", "router"])
            except OSError as e:
                self.logger.log("router_plan_write_failed", error=str(e))
        self._stage_order = list(stage_order)

        plan_graph.nodes = []
        for sid in stage_order[:10]:
            plan_graph.add_node(PlanNode(id=f"n_{sid}", name=sid, modeSegment="main"))
        for a, b in zip(stage_order, stage_order[1:]):
            plan_graph.add_edge(f"n_{a}", f"n_{b}")
        gates: List[StageGate] = [
            StageGate(id="sg_api_contract", name="API contract passes", conditions=["tests.pass('api_contract')"]),
            StageGate(id="sg_be_scaffold", name="Backend scaffold present", conditions=["tests.pass('api_contract') and artifact.exists('backend/**')"]),
            StageGate(id="sg_fe_scaffold", name="Frontend scaffold present", conditions=["artifact.exists('frontend/**') or artifact.exists('public/index.html')"]),
            StageGate(id="sg_smoke", name="Smoke tests pass", conditions=["tests.pass('smoke_suite')"]),
        ]
        evaluator = GateEvaluator(self.run_dir, self.artifacts, self.logger, workspace_root=self.cwd, run_started_at=self._workspace_baseline_at)
        runner = ContractRunner(self.run_dir, self.logger, workspace_root=self.cwd, run_started_at=self._workspace_baseline_at)

        agents: Dict[str, Any] = {}
        def ensure_agent(role: str):
            if role in agents:
                return agents[role]
            if role == "frontend":
                agents[role] = FrontendAgent("frontend", self.cfg, self.logger, self.artifacts, self.rag, workspace_root=self.cwd)
            elif role == "backend":
                agents[role] = BackendAgent("backend", self.cfg, self.logger, self.artifacts, self.rag, workspace_root=self.cwd)
            elif role == "llmapi":
                agents[role] = LLMApiAgent("llmapi", self.cfg, self.logger, self.artifacts, self.rag, workspace_root=self.cwd)
            elif role == "tests":
                agents[role] = TestAgent("tests", self.cfg, self.logger, self.artifacts, self.rag, workspace_root=self.cwd)
            return agents.get(role)

        def get_agent(role: str):
            return agents.get(role)

        decisions: List[DecisionSummary] = []
        injected_by_target: Dict[str, List[str]] = {}
        unread_huddles: List[Dict[str, Any]] = []
        current_step: str = stage_order[0] if stage_order else "contracts"
        self._current_step = current_step

        tools = self._build_tools_manifest()

        system_msg = {"role": "system", "content": self._router_system_prompt()}
        init_state = self._snapshot_state(plan_graph, evaluator, decisions, unread_huddles, tools)
        user_msg = {
            "role": "user",
            "content": (
                "Goal: " + (goal or "") + "\n\n" +
                "State: " + json.dumps(init_state, ensure_ascii=False) + "\n\n" +
                "Manager plan: stages=" + json.dumps(stage_order) + "; current_step=" + str(current_step) + "; mode=" + str(self.mode) + "\n" +
                "Instruction: Use ladder-style milestone progression via propose_advance_step, but feel free to run tracks (schedule_slice with multiple agents) within a stage when helpful. Use open_huddle as manager check-ins when blocked or after major slices to decide whether to adapt mode/plan."
            )
        }
        messages: List[Dict[str, Any]] = [system_msg, user_msg]

        finalized = False
        max_steps = getattr(self.cfg, "router_max_steps", 32) or 32
        step_idx = 0
        no_tool_streak = 0

        while step_idx < max_steps and not finalized:
            step_idx += 1
            self._current_step = current_step
            out = rllm._call_with_tools(messages, tools=tools, phase="agentic", tool_choice="auto")
            tool_calls = out.get("tool_calls") or []
            transcript.add_model_call(
                title=f"Router Agentic Turn #{step_idx}",
                provider=out.get("provider") or "?",
                model=out.get("model") or "?",
                messages=messages[-6:],
                output=out.get("text"),
                tools_offered=tools,
                tool_choice="auto",
                tool_calls=tool_calls,
            )

            if not tool_calls:
                no_tool_streak += 1
                if no_tool_streak >= 2:
                    messages.append({"role": "system", "content": "Reminder: You must act via tools. Pick exactly one tool now."})
                if out.get("api") == "responses":
                    messages.append({"role": "assistant", "content": out.get("text"), "_skip_for_responses": True})
                else:
                    messages.append({"role": "assistant", "content": out.get("text")})
                continue
            no_tool_streak = 0

            if out.get("api") == "responses":
                messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls, "_skip_for_responses": True})
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})

            for tc in tool_calls:
                tool_name = (tc or {}).get("function", {}).get("name") or ""
                tool_args_s = (tc or {}).get("function", {}).get("arguments") or "{}"
                try:
                    tool_args = json.loads(tool_args_s)
                except json.JSONDecodeError:
                    tool_args = {}

                def _finish_tool(_obs: Dict[str, Any], _err: Optional[str]) -> None:
                    self.logger.log(
                        "router_tool_call",
                        tool_name=tool_name,
                        params=tool_args,
                        observation=(_obs if len(str(_obs)) < 5000 else {"note": "obs too large"}),
                        error=_err,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": (tc.get("id") if isinstance(tc, dict) else None),
                        "name": tool_name,
                        "content": json.dumps(_obs, ensure_ascii=False),
                    })

                obs: Dict[str, Any] = {}
                err: Optional[str] = None
                try:
                    if not self._checklist_created and tool_name not in ("create_checklist", "set_mode"):
                        obs = {"error": "checklist_required", "note": "Call create_checklist before other tools."}
                        self.logger.log("checklist_required", tool=tool_name)
                        messages.append({"role": "system", "content": "Guardrail: create_checklist must be called before other tools."})
                        _finish_tool(obs, None)
                        continue
                    if tool_name == "set_mode":
                        target = (tool_args.get("target_mode") or "").strip().lower()
                        reason = tool_args.get("reason") or ""
                        applied = target in ("ladder", "tracks", "weave")
                        if applied:
                            prev = self.mode
                            self.mode = target
                            plan_graph.mode_by_segment = ({"critical": "ladder", "docs": "tracks"} if self.mode == "weave" else {"main": self.mode})
                            self.logger.log("mode_decision", previous=prev, current=self.mode, reason=reason)
                            transcript.add_info("Router Decision", f"Mode set to {self.mode}. Reason: {reason}")
                            obs = {"applied": True, "current_mode": self.mode, "note": "mode updated"}
                        else:
                            obs = {"applied": False, "current_mode": self.mode, "note": "invalid target_mode"}
                    elif tool_name == "open_huddle":
                        topic = tool_args.get("topic") or self._huddle_topic(goal)
                        raw_att = tool_args.get("attendees") or []
                        agenda = tool_args.get("agenda") or ""
                        q_in = tool_args.get("questions")
                        questions: List[str] = []
                        if isinstance(q_in, list):
                            questions = [str(q).strip() for q in q_in if str(q).strip()]
                        if (not raw_att) and unread_huddles:
                            last = unread_huddles[-1]
                            if isinstance(last, dict):
                                if last.get("attendees"):
                                    raw_att = last.get("attendees") or []
                                if not questions:
                                    last_q = last.get("questions") or []
                                    if isinstance(last_q, list):
                                        questions = [str(x).strip() for x in last_q if str(x).strip()]
                        norm_att: List[str] = ["router"]
                        missing_agents: List[str] = []
                        include_roles: List[str] = []
                        for a in raw_att:
                            s = str(a).strip()
                            if not s:
                                continue
                            if s == "router":
                                if "router" not in norm_att:
                                    norm_att.append("router")
                                continue
                            if not s.startswith("agent:"):
                                s = f"agent:{s}"
                            if s not in norm_att:
                                norm_att.append(s)
                            role = s.split("agent:", 1)[-1]
                            if role and role not in agents:
                                missing_agents.append(s)
                            if role and role not in include_roles:
                                include_roles.append(role)
                        if missing_agents or not agents:
                            obs = {
                                "error": "huddle_requires_agents",
                                "missing_agents": missing_agents,
                                "note": "Call spawn_agents before open_huddle.",
                            }
                            self.logger.log("huddle_blocked", reason="agents_not_spawned", missing=missing_agents)
                            messages.append({"role": "system", "content": "Guardrail: open_huddle requires spawn_agents first. Spawn missing agents, then retry."})
                            _finish_tool(obs, None)
                            continue
                        if len(unread_huddles) >= self._max_open_huddles:
                            self.logger.log("huddle_limit", max_open=self._max_open_huddles, topic=topic, attendees=norm_att, note="executing check-in anyway")
                        agent_reports: List[Dict[str, Any]] = []
                        for role, ag in agents.items():
                            agent_reports.append({"agent": role, **asdict(ag.report())})
                        state_blob = {
                            "mode": self.mode,
                            "current_step": current_step,
                            "checklist": [i.to_dict() for i in self._checklist.items],
                            "latest_tests": evaluator.latest_tests,
                            "latest_gates": [asdict(g) for g in (self._latest_gate_results or [])],
                            "agent_reports": agent_reports,
                        }
                        proposed = "Manager check-in context:\n" + json.dumps(state_blob, ensure_ascii=False)[:6000]
                        hud = self._execute_huddle(
                            topic=topic,
                            questions=(questions or ([agenda] if agenda else ["Status? blockers? do we need to switch ladder vs tracks?"])),
                            proposed_contract=proposed,
                            transcript=transcript,
                            agents=agents,
                            decisions_so_far=decisions,
                            include_agents=(include_roles or None),
                        )
                        new_decisions = hud.get("decisions") or []
                        if isinstance(new_decisions, list):
                            decisions.extend(new_decisions)
                            upd = self._apply_plan_updates_from_decisions(new_decisions)
                            if upd.get("mode"):
                                plan_graph.add_reason("mode_change", f"huddle switched mode to {upd.get('mode')}")
                            if upd.get("stage_order"):
                                stage_order = list(self._stage_order)
                                plan_graph.add_reason("plan_change", "huddle updated stage_order")
                            unread_huddles.clear()
                        obs = {
                            "huddle_id": (hud.get("huddle_id") or None),
                            "decisions": [getattr(d, "id", None) for d in (new_decisions or [])],
                            "applied_mode": self.mode,
                            "transcript_path": hud.get("transcript_path"),
                            "summary_path": hud.get("summary_path"),
                        }
                    elif tool_name == "request_huddle":
                        topic = str(tool_args.get("topic") or "").strip() or self._huddle_topic(goal)
                        q = tool_args.get("questions") or []
                        if isinstance(q, str) and q.strip():
                            questions = [q.strip()]
                        elif isinstance(q, list):
                            questions = [str(x).strip() for x in q if str(x).strip()]
                        else:
                            questions = []
                        att = tool_args.get("attendees")
                        attendees = None
                        if isinstance(att, str) and att.strip():
                            attendees = [att.strip()]
                        elif isinstance(att, list):
                            attendees = [str(x).strip() for x in att if str(x).strip()]
                        agenda = tool_args.get("agenda")
                        if agenda and not questions:
                            questions = [str(agenda).strip()]
                        frm = tool_args.get("from") or "router"
                        urgency = tool_args.get("urgency") or "normal"
                        req = {"from": str(frm), "topic": topic, "questions": questions, "attendees": attendees, "urgency": str(urgency)}
                        unread_huddles.append(req)
                        self.logger.log("huddle_request_queued", **req)
                        obs = {"queued": True, "pending": len(unread_huddles), "topic": topic, "attendees": attendees}
                    elif tool_name == "record_decision_summary":
                        d_obj = {
                            "topic": tool_args.get("topic") or "",
                            "options": tool_args.get("options") or [],
                            "decision": tool_args.get("decision"),
                            "rationale": tool_args.get("rationale"),
                            "risks": tool_args.get("risks") or [],
                            "actions": tool_args.get("actions") or [],
                            "contracts": tool_args.get("contracts") or [],
                            "links": tool_args.get("links") or [],
                            "sources": tool_args.get("sources") or None,
                        }
                        hud_id = tool_args.get("huddle_id")
                        if not hud_id and unread_huddles:
                            last_hud = unread_huddles[-1]
                            if isinstance(last_hud, dict):
                                hud_id = (last_hud or {}).get("huddle_id")
                        transcript_rel = None
                        rec_obj = None
                        if hud_id:
                            rec_path = os.path.join(self.run_dir, "artifacts", "huddles", f"{hud_id}.json")
                            if os.path.exists(rec_path):
                                try:
                                    with open(rec_path, "r", encoding="utf-8") as f:
                                        rec_obj = json.load(f)
                                    transcript_rel = rec_obj.get("transcript_path")
                                except (OSError, ValueError):
                                    rec_obj = None
                        ds = parse_decision_summaries(json.dumps(d_obj))[0]
                        from .huddle import _normalize_sources
                        base_sources = _normalize_sources(ds.sources)
                        externals = [s for s in base_sources if s.get("type") == "external"]
                        if len(externals) < 3 and self._web_recent:
                            picks: List[Dict[str, Any]] = []
                            seen = {("external", s.get("url")) for s in externals if s.get("url")}
                            for r in list(self._web_recent)[-5:]:
                                obsr = r.get("obs") or {}
                                for it in (obsr.get("results") or [])[:5]:
                                    u = (it or {}).get("url")
                                    if not u:
                                        continue
                                    key = ("external", u)
                                    if key in seen:
                                        continue
                                    seen.add(key)
                                    title = (it or {}).get("title") or None
                                    picks.append({"type": "external", "url": u, **({"title": title} if title else {})})
                                    if len(externals) + len(picks) >= 3:
                                        break
                                if len(externals) + len(picks) >= 3:
                                    break
                            if picks:
                                base_sources = base_sources + picks
                                ds.sources = base_sources
                                if not isinstance(ds.meta, dict):
                                    ds.meta = {}
                                ds.meta["auto_populated_sources"] = True
                        if transcript_rel:
                            ds.links = (ds.links or []) + [{"type": "artifact", "id": transcript_rel, "title": "Huddle Transcript"}]
                            base_sources2 = list(ds.sources) if isinstance(ds.sources, list) else []
                            base_sources2.append({"type": "artifact", "id": transcript_rel})
                            ds.sources = base_sources2

                        def _autopopulate_from_recent(ds_obj):
                            if ds_obj.sources:
                                return ds_obj
                            recent = list(self._web_recent)[-3:]
                            if not recent:
                                return ds_obj
                            urls_ranked: List[Dict[str, Any]] = []
                            for r in recent:
                                obsr = r.get("obs") or {}
                                res = obsr.get("results") or []
                                exs = obsr.get("extracts") or []
                                ok_urls = {e.get("url") for e in exs if isinstance(e, dict) and str(e.get("status")) == "200" and (e.get("content_md") or "")}
                                for it in res:
                                    u = (it or {}).get("url")
                                    if not u:
                                        continue
                                    title = (it or {}).get("title") or None
                                    ts_val = (it or {}).get("time?") or None
                                    score = 1 + (5 if u in ok_urls else 0)
                                    urls_ranked.append({"url": u, "title": title, "ts": ts_val, "score": score})
                            if not urls_ranked:
                                return ds_obj
                            seen: set = set()
                            picks: List[Dict[str, Any]] = []
                            for it in sorted(urls_ranked, key=lambda x: x.get("score", 0), reverse=True):
                                u = it.get("url")
                                if u in seen:
                                    continue
                                seen.add(u)
                                picks.append({"type": "external", "url": u, **({"title": it.get("title")} if it.get("title") else {}), **({"ts": it.get("ts")} if it.get("ts") else {})})
                                if len(picks) >= 5:
                                    break
                            if picks:
                                ds_obj.sources = (ds_obj.sources or []) + picks
                                if not isinstance(ds_obj.meta, dict):
                                    ds_obj.meta = {}
                                ds_obj.meta["auto_populated_sources"] = True
                            return ds_obj

                        if not ds.sources:
                            ds = _autopopulate_from_recent(ds)

                        from .huddle import persist_decision_summary
                        ds, ds_rel = persist_decision_summary(self.run_dir, self.artifacts, self.rag, ds)
                        decisions.append(ds)
                        self.logger.log("decision_summary_updated", ds_id=ds.id, fields_updated=["sources", "links", "meta"], path=os.path.join(self.run_dir, ds_rel))
                        if hud_id:
                            rec_path = os.path.join(self.run_dir, "artifacts", "huddles", f"{hud_id}.json")
                            if os.path.exists(rec_path):
                                with open(rec_path, "r", encoding="utf-8") as f:
                                    rec_obj = json.load(f)
                                decs = rec_obj.get("decisions") or []
                                if ds.id not in decs:
                                    decs.append(ds.id)
                                rec_obj["decisions"] = decs
                                with open(rec_path, "w", encoding="utf-8") as f:
                                    json.dump(rec_obj, f, indent=2)
                        self.logger.log("decision_summary", decision_id=ds.id, topic=ds.topic, decision=ds.decision)
                        injected = ["router"]
                        transcript.add_decision_injection(decision_injection_text([ds]))
                        summary_line = f"{ds.topic}: {ds.decision or ''}".strip()
                        transcript.add_info("Router Decision", summary_line)
                        for role in list(agents.keys()):
                            injected.append(f"agent:{role}")
                        self.logger.log("decision_injected", decision_id=ds.id, targets=injected)
                        obs = {"decision_id": ds.id, "injected_into": injected}
                        _finish_tool(obs, err)
                        continue
                    elif tool_name == "inject_summary":
                        ds_id = tool_args.get("decision_id") or ""
                        targets = tool_args.get("targets") or []
                        for t in targets:
                            injected_by_target.setdefault(t, [])
                            if ds_id not in injected_by_target[t]:
                                injected_by_target[t].append(ds_id)
                        dd = next((d for d in decisions if getattr(d, "id", None) == ds_id), None)
                        if dd:
                            transcript.add_decision_injection(decision_injection_text([dd]))
                        self.logger.log("decision_injected", decision_id=ds_id, targets=targets)
                        obs = {"targets_injected": list(targets)}
                    elif tool_name == "update_checklist":
                        if not self._checklist_created:
                            obs = {"error": "checklist_not_created", "note": "Call create_checklist first."}
                            self.logger.log("checklist_update_blocked", reason="not_created")
                            messages.append({"role": "system", "content": "Guardrail: create_checklist must be called before update_checklist."})
                            _finish_tool(obs, None)
                            continue
                        updates = tool_args.get("items") or []
                        changed = self._checklist.update_items(updates if isinstance(updates, list) else [], allow_create=True)
                        self._save_checklist()
                        transcript.add_info("Router Decision", f"Checklist updated: {', '.join(changed) if changed else 'no changes'}")
                        obs = {
                            "updated": changed,
                            "checklist": [i.to_dict() for i in self._checklist.items],
                        }
                    elif tool_name == "create_checklist":
                        items = tool_args.get("items") or []
                        if not isinstance(items, list):
                            items = []
                        created = self._checklist.replace_items(items)
                        self._checklist_created = True
                        self._save_checklist()
                        self.logger.log("checklist_created", items=created)
                        transcript.add_info("Router Decision", f"Created checklist with {len(created)} items.")
                        obs = {"created": created, "checklist": [i.to_dict() for i in self._checklist.items]}
                    elif tool_name == "spawn_agents":
                        roles = [str(r) for r in (tool_args.get("roles") or [])]
                        spawned: List[str] = []
                        already: List[str] = []
                        for r in roles:
                            a = ensure_agent(r)
                            if a is None:
                                continue
                            key = f"agent:{r}"
                            if key in spawned or key in already:
                                already.append(key)
                            else:
                                spawned.append(key)
                        if spawned:
                            self._saw_agents_spawned = True
                        self.logger.log("agents_spawned", spawned=spawned, already_active=already)
                        obs = {"spawned": spawned, "already_active": already}
                    elif tool_name == "destroy_agents":
                        roles = [str(r).replace("agent:", "").strip() for r in (tool_args.get("roles") or []) if str(r).strip()]
                        destroyed: List[str] = []
                        missing: List[str] = []
                        for r in roles:
                            if r in agents:
                                agents.pop(r, None)
                                destroyed.append(f"agent:{r}")
                            else:
                                missing.append(f"agent:{r}")
                        self.logger.log("agents_destroyed", destroyed=destroyed, missing=missing, reason=tool_args.get("reason"))
                        obs = {"destroyed": destroyed, "missing": missing}
                    elif tool_name == "set_agent_permissions":
                        role = str(tool_args.get("role") or "").replace("agent:", "").strip()
                        modep = str(tool_args.get("mode") or "set").strip().lower()
                        allow = tool_args.get("allow_globs") or []
                        deny = tool_args.get("deny_globs") or []
                        if role not in ("backend", "frontend", "llmapi", "tests"):
                            obs = {"error": "unknown_role", "role": role}
                        else:
                            cur = self._agent_permissions.get(role) or {"allow_globs": [], "deny_globs": []}
                            if modep == "set":
                                if allow is not None:
                                    cur["allow_globs"] = [str(x) for x in (allow or []) if str(x).strip()]
                                if deny is not None:
                                    cur["deny_globs"] = [str(x) for x in (deny or []) if str(x).strip()]
                            elif modep == "add":
                                cur["allow_globs"] = sorted(set(list(cur.get("allow_globs") or []) + [str(x) for x in (allow or []) if str(x).strip()]))
                                cur["deny_globs"] = sorted(set(list(cur.get("deny_globs") or []) + [str(x) for x in (deny or []) if str(x).strip()]))
                            elif modep == "remove":
                                cur["allow_globs"] = [x for x in (cur.get("allow_globs") or []) if x not in set([str(x) for x in (allow or [])])]
                                cur["deny_globs"] = [x for x in (cur.get("deny_globs") or []) if x not in set([str(x) for x in (deny or [])])]
                            self._agent_permissions[role] = cur
                            if role in agents and self.mode == "tracks":
                                agents[role].set_write_policy(allow_globs=cur.get("allow_globs"), deny_globs=cur.get("deny_globs"))
                            self.logger.log("agent_permissions_set", role=role, mode=modep, allow_globs=cur.get("allow_globs"), deny_globs=cur.get("deny_globs"), reason=tool_args.get("reason"))
                            obs = {"role": role, "permissions": cur, "mode": self.mode}
                    elif tool_name == "get_agent_permissions":
                        role = tool_args.get("role")
                        if role:
                            role = str(role).replace("agent:", "").strip()
                        if role:
                            obs = {"role": role, "permissions": self._agent_permissions.get(role)}
                        else:
                            obs = {"permissions": self._agent_permissions}
                    elif tool_name == "schedule_slice":
                        actives = [str(x) for x in (tool_args.get("active_agents") or [])]
                        parallel = bool(tool_args.get("parallel")) or (self.mode == "tracks")
                        try:
                            slice_timeout_sec = int(tool_args.get("timeout_sec") or 300)
                        except (TypeError, ValueError):
                            slice_timeout_sec = 300
                        slice_timeout_sec = max(5, min(1800, slice_timeout_sec))
                        artifacts_written: List[str] = []
                        artifact_refs_out: List[Dict[str, Any]] = []
                        reports: List[Dict[str, Any]] = []
                        errors: List[str] = []
                        auto_added: List[str] = []
                        skipped_agents: List[str] = []
                        if not agents:
                            obs = {"error": "schedule_slice_requires_agents", "note": "Call spawn_agents before schedule_slice."}
                            self.logger.log("slice_blocked", reason="agents_not_spawned")
                            messages.append({"role": "system", "content": "Guardrail: schedule_slice requires spawn_agents first. Spawn agents and retry."})
                            _finish_tool(obs, None)
                            continue
                        if len(actives) > self._max_slice_agents:
                            skipped_agents = actives[self._max_slice_agents:]
                            actives = actives[: self._max_slice_agents]
                            self.logger.log("slice_limit", max_agents=self._max_slice_agents, skipped=skipped_agents)

                        def _run_one(an: str) -> Dict[str, Any]:
                            role = an.replace("agent:", "").strip()
                            ag = get_agent(role)
                            if ag is None:
                                return {"agent": an, "error": f"agent not spawned: {an}", "artifacts": [], "report": None, "needs_huddle": False}

                            if self.mode == "tracks" and role in self._agent_permissions:
                                pol = self._agent_permissions.get(role) or {}
                                ag.set_write_policy(allow_globs=pol.get("allow_globs"), deny_globs=pol.get("deny_globs"))
                            else:
                                ag.set_write_policy(allow_globs=None, deny_globs=[])

                            ctx = self._agent_context(goal, decisions)
                            plan_err = None
                            act_err = None
                            refs: List[ArtifactRef] = []
                            hud_reqs: List[Dict[str, Any]] = []
                            try:
                                _ = ag.plan(current_step, ctx)
                            except ProviderError as e:
                                plan_err = str(e)
                            try:
                                refs = ag.act(ctx)
                            except ProviderError as e:
                                act_err = str(e)
                            if hasattr(ag, "drain_huddle_requests"):
                                hud_reqs = list(getattr(ag, "drain_huddle_requests")() or [])
                            rep = None
                            rep = asdict(ag.report())
                            nh = False
                            if hasattr(ag, "needs_huddle") and ag.needs_huddle(ctx):
                                nh = True
                            return {
                                "agent": an,
                                "role": role,
                                "plan_error": plan_err,
                                "act_error": act_err,
                                "artifacts": [r.path for r in refs],
                                "report": rep,
                                "needs_huddle": nh,
                                "huddle_requests": hud_reqs,
                                "refs": refs,
                            }

                        results_one: List[Dict[str, Any]] = []
                        import concurrent.futures
                        max_workers = len(actives) if (parallel and len(actives) > 1) else 1
                        timed_out = False
                        ex = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
                        fut_map: Dict[concurrent.futures.Future, str] = {}
                        try:
                            for an in actives:
                                fut_map[ex.submit(_run_one, an)] = an
                            done, not_done = concurrent.futures.wait(set(fut_map.keys()), timeout=slice_timeout_sec)
                            for fut in done:
                                try:
                                    results_one.append(fut.result())
                                except Exception as e:
                                    results_one.append({"agent": fut_map.get(fut, "unknown"), "error": str(e), "artifacts": [], "report": None, "needs_huddle": False})
                            if not_done:
                                timed_out = True
                                for fut in not_done:
                                    an = fut_map.get(fut, "unknown")
                                    fut.cancel()
                                    results_one.append({"agent": an, "error": f"slice_timeout_after_{slice_timeout_sec}s", "artifacts": [], "report": None, "needs_huddle": False})
                                self.logger.log("slice_timeout", timeout_sec=slice_timeout_sec, agents=[fut_map.get(f, "unknown") for f in not_done])
                        finally:
                            try:
                                ex.shutdown(wait=(not timed_out), cancel_futures=timed_out)
                            except TypeError:
                                ex.shutdown(wait=(not timed_out))

                        for rr in results_one:
                            if rr.get("error"):
                                errors.append(str(rr.get("error")))
                                continue
                            if rr.get("plan_error"):
                                errors.append(f"plan error {rr.get('agent')}: {rr.get('plan_error')}")
                            if rr.get("act_error"):
                                errors.append(f"act error {rr.get('agent')}: {rr.get('act_error')}")
                            arts = rr.get("artifacts") or []
                            artifacts_written.extend([str(p) for p in arts])
                            refs = rr.get("refs") or []
                            for ref in refs:
                                artifact_refs_out.append(
                                    {
                                        "agent": str(rr.get("role") or rr.get("agent") or "?"),
                                        "path": getattr(ref, "path", None),
                                        "sha256": getattr(ref, "sha256", None),
                                        "tags": getattr(ref, "tags", None),
                                        "meta": getattr(ref, "meta", None),
                                    }
                                )
                                self.logger.log(
                                    "slice_artifact",
                                    agent=str(rr.get("role") or rr.get("agent") or "?"),
                                    path=getattr(ref, "path", None),
                                    sha256=getattr(ref, "sha256", None),
                                    tags=getattr(ref, "tags", None),
                                    meta=getattr(ref, "meta", None),
                                )
                            for p in arts:
                                self._record_touched(p)
                            role = rr.get("role") or rr.get("agent") or "?"
                            self.logger.log("agent_turn", agent=str(role), artifacts=list(arts))
                            if rr.get("report"):
                                reports.append(rr.get("report"))
                            if rr.get("needs_huddle"):
                                unread_huddles.append({"from": rr.get("agent"), "topic": self._huddle_topic(goal)})
                            for rq in (rr.get("huddle_requests") or []):
                                if isinstance(rq, dict) and rq.get("topic"):
                                    unread_huddles.append(rq)
                        try:
                            results = runner.scan_and_run(allow_commands=True, command_validator=self._validate_command)
                            gate_results = evaluator.evaluate(gates)
                            self._latest_gate_results = gate_results
                            for r in results:
                                if (r.metrics or {}).get("test_type") == "command":
                                    self._saw_command_test_result = True
                        except Exception as e:
                            errors.append(f"contract_runner_error: {e}")
                            results = []
                        if errors:
                            obs = {
                                "error": "schedule_slice_errors",
                                "errors": errors,
                                "partial": {
                                    "artifacts_written": artifacts_written,
                                    "reports": reports,
                                    "auto_added": auto_added,
                                    "skipped_agents": skipped_agents,
                                },
                            }
                            messages.append({"role": "system", "content": "Guardrail: schedule_slice returned errors. Fix missing agents or failures, then retry."})
                        else:
                            self._saw_schedule_slice = True
                            obs = {"artifacts_written": artifacts_written, "artifact_refs": artifact_refs_out[-200:], "reports": reports, "errors": errors, "auto_added": auto_added, "skipped_agents": skipped_agents}
                            try:
                                findings = run_coherence_checks(
                                    self.cwd,
                                    paths=sorted(list(self._touched_files)) if self._touched_files else None,
                                    changed_since=(self._workspace_baseline_at or self._run_started_at),
                                )
                                highs = [f for f in findings if isinstance(f, dict) and f.get("severity") == "high"]
                                if highs:
                                    for f in highs[:3]:
                                        unread_huddles.append({
                                            "from": "coherence",
                                            "topic": f"Coherence issue: {f.get('id')}",
                                            "questions": [str(f.get("message") or "")],
                                            "attendees": f.get("suggested_attendees"),
                                            "urgency": "high",
                                        })
                                    self.logger.log("coherence_issues", findings=highs[:10])
                                    messages.append({"role": "system", "content": "Coherence checks found high-severity issues. Consider open_huddle to resolve: " + json.dumps([h.get('id') for h in highs[:3]])})
                            except OSError as e:
                                self.logger.log("coherence_checks_failed", error=str(e))
                            failing = [g.id for g in (self._latest_gate_results or []) if g.status != "passed"]
                            if failing or unread_huddles:
                                messages.append({
                                    "role": "system",
                                    "content": "Manager note: consider open_huddle to adapt plan/mode. failing_gates=" + json.dumps(failing) + " unread_huddles=" + json.dumps(unread_huddles[-3:]),
                                })
                        self._sync_checklist_state()
                        self._save_checklist()
                    elif tool_name == "rag_search":
                        q = tool_args.get("query") or ""
                        k = int(tool_args.get("top_k") or 5)
                        where = tool_args.get("where") if isinstance(tool_args, dict) else None
                        hits = self.rag.search(q, top_k=k, where=(where if isinstance(where, dict) else None))
                        self.logger.log("rag_search", role="router", q=q, top_k=k, hits=[h.get("doc_id") for h in hits])
                        obs = {"hits": [
                            {
                                "doc_id": h.get("doc_id"),
                                "score": h.get("score"),
                                "path": h.get("path"),
                                "tags": h.get("tags"),
                                "meta": h.get("meta"),
                                "snippet_or_path": (h.get("path") or h.get("snippet")),
                            } for h in hits
                        ]}
                    elif tool_name == "semantic_search":
                        q = tool_args.get("query") or ""
                        k = int(tool_args.get("top_k") or 5)
                        where = tool_args.get("where") if isinstance(tool_args, dict) else None
                        hits = self.rag.search_semantic(q, top_k=k, where=(where if isinstance(where, dict) else None))
                        self.logger.log("semantic_search", role="router", q=q, top_k=k, hits=[h.get("doc_id") for h in hits])
                        obs = {"hits": [
                            {
                                "doc_id": h.get("doc_id"),
                                "score": h.get("score"),
                                "path": h.get("path"),
                                "tags": h.get("tags"),
                                "meta": h.get("meta"),
                                "snippet_or_path": (h.get("path") or h.get("snippet")),
                            } for h in hits
                        ]}
                    elif tool_name == "coherence_check":
                        mx = tool_args.get("max_findings")
                        try:
                            mxn = int(mx) if mx is not None else 20
                        except (TypeError, ValueError):
                            mxn = 20
                        paths = tool_args.get("paths") if isinstance(tool_args, dict) else None
                        changed_since = tool_args.get("changed_since") if isinstance(tool_args, dict) else None
                        try:
                            cs = float(changed_since) if changed_since is not None else None
                        except (TypeError, ValueError):
                            cs = None
                        findings = run_coherence_checks(self.cwd, paths=(paths if isinstance(paths, list) else None), changed_since=cs)
                        obs = {"findings": findings[: max(1, min(mxn, 50))]}
                    elif tool_name == "web_search":
                        q = tool_args.get("query") or ""
                        k = int(tool_args.get("top_k") or 5)
                        tr = tool_args.get("time_range")
                        eng = tool_args.get("engines")
                        lang = tool_args.get("language")
                        pageno = tool_args.get("pageno")
                        obs = self._web_search_exec(q, k, tr, eng, lang, pageno)
                        hud_ctx = None
                        if unread_huddles:
                            last_hud = unread_huddles[-1]
                            if isinstance(last_hud, dict):
                                hud_ctx = (last_hud or {}).get("huddle_id")
                        entry = {"ts": time.time(), "huddle_id": hud_ctx, "query": q, "obs": obs}
                        self._web_recent.append(entry)
                        if len(self._web_recent) > 5:
                            self._web_recent = self._web_recent[-5:]
                    elif tool_name == "run_contract_tests":
                        tests = [str(t) for t in (tool_args.get("tests") or [])]
                        specs = runner.scan_specs()
                        available_ids = [s.get("id") for s in specs if isinstance(s, dict) and s.get("id")]
                        selected_specs = specs
                        if tests:
                            want = set(tests)
                            selected_specs = [s for s in specs if (s.get("id") in want)]

                        results: List[Any] = []
                        want_api_contract = (not tests) or ("api_contract" in tests)
                        if want_api_contract:
                            results.extend(runner.scan_and_run(allow_commands=False, command_validator=None))
                            if tests:
                                want = set(tests)
                                results = [r for r in results if (r.id in want) or (r.id == "api_contract")]

                        if not specs and not results:
                            results = runner.scan_and_run(allow_commands=False, command_validator=None)
                            if tests:
                                want = set(tests)
                                results = [r for r in results if r.id in want]
                        elif specs:
                            extra = runner.run_specs(selected_specs, allow_commands=True, command_validator=self._validate_command) if selected_specs else []
                            results.extend(extra)

                        if not results:
                            obs = {
                                "error": "no_contract_tests",
                                "note": "No contract tests matched. Create a contract test manifest in contracts/tests/*.json or ensure an OpenAPI spec exists for auto schema validation.",
                                "requested_tests": tests,
                                "available_tests": available_ids,
                            }
                            self.logger.log("contract_tests_missing", requested=tests, available=available_ids)
                            messages.append({"role": "system", "content": "Guardrail: run_contract_tests found no matching tests. If you haven't created contracts/tests/*.json yet, write contracts/openapi.yaml first and retry (auto schema check will run), or generate explicit contract tests via the tests agent."})
                        else:
                            self._saw_run_contract_tests = True
                            for r in results:
                                if (r.metrics or {}).get("test_type") == "command":
                                    self._saw_command_test_result = True
                            gate_results = evaluator.evaluate(gates)
                            self._latest_gate_results = gate_results
                            obs = {"results": [asdict(r) for r in results], "created_tests": False}
                        self._sync_checklist_state()
                        self._save_checklist()
                    elif tool_name == "propose_advance_step":
                        step_id_raw = tool_args.get("step_id") or current_step
                        gate_to_step = {
                            "sg_api_contract": "contracts",
                            "sg_be_scaffold": "backend_scaffold",
                            "sg_fe_scaffold": "frontend_scaffold",
                            "sg_smoke": "smoke_tests",
                        }
                        requested_step_id = gate_to_step.get(step_id_raw, step_id_raw)

                        def _canonical_step_for_gates(step: str) -> str:
                            s = (step or "").strip().lower()
                            if s in ("contracts", "backend_scaffold", "frontend_scaffold", "smoke_tests"):
                                return s
                            if any(k in s for k in ("contract", "plan", "arch", "design", "spec")):
                                return "contracts"
                            if "front" in s or "ui" in s or "web" in s:
                                return "frontend_scaffold"
                            if "back" in s or "api" in s or "server" in s:
                                return "backend_scaffold"
                            if "test" in s or "smoke" in s or "deploy" in s or "qa" in s:
                                return "smoke_tests"
                            return ""

                        step_id_for_gates = _canonical_step_for_gates(requested_step_id)
                        valid_steps = list(stage_order) if isinstance(stage_order, list) and stage_order else ["contracts", "backend_scaffold", "frontend_scaffold", "smoke_tests"]
                        if requested_step_id not in valid_steps:
                            obs = {"advanced": False, "error": "unknown_step", "step_id": step_id_raw}
                            self.logger.log("advance_rejected", reason="unknown_step", requested=step_id_raw)
                            _finish_tool(obs, None)
                            continue
                        def gates_for(step: str) -> List[StageGate]:
                            if step == "contracts":
                                return [g for g in gates if g.id == 'sg_api_contract']
                            if step == "backend_scaffold":
                                return [g for g in gates if g.id in ('sg_api_contract','sg_be_scaffold')]
                            if step == "frontend_scaffold":
                                return [g for g in gates if g.id in ('sg_fe_scaffold',)]
                            if step == "smoke_tests":
                                return [g for g in gates if g.id in ('sg_smoke','sg_fe_scaffold','sg_be_scaffold','sg_api_contract')]
                            return []
                        runner.scan_and_run(allow_commands=False, command_validator=None)
                        gres = evaluator.evaluate(gates_for(step_id_for_gates))
                        if all(g.status == "passed" for g in gres):
                            chk = self._checkins_by_stage.get(requested_step_id) or {}
                            when = str(chk.get("when") or "").strip().lower()
                            if when in ("after", "both"):
                                ctopic = (chk.get("topic") or "").strip() or f"Check-in after {requested_step_id}"
                                unread_huddles.append({"from": "scheduled", "topic": ctopic, "questions": [f"Check-in after stage {requested_step_id}: status, blockers, mode/plan changes?"]})
                                self.logger.log("huddle_checkin_scheduled", stage=requested_step_id, when=when, topic=ctopic)
                            order = valid_steps
                            try:
                                idx = order.index(requested_step_id)
                                next_step = order[min(idx + 1, len(order) - 1)]
                            except ValueError:
                                next_step = requested_step_id
                            current_step = next_step
                            self._current_step = current_step
                            if requested_step_id in self._gate_failures:
                                self._gate_failures.pop(requested_step_id, None)
                            obs = {"advanced": True, "next_step": next_step, "order": order, "gates_step": step_id_for_gates}
                        else:
                            import time as _t
                            now = _t.time()
                            cnt, last = self._gate_failures.get(requested_step_id, (0, 0.0))
                            cnt = cnt + 1
                            self._gate_failures[requested_step_id] = (cnt, now)
                            cooldown_active = (cnt >= self._cooldown_threshold) and ((now - last) <= 60.0)
                            retry_after_ms = int(self._cooldown_seconds * 1000) if cooldown_active else 0
                            if cooldown_active:
                                self.logger.log("router_cooldown", step=requested_step_id, failures=cnt, retry_after_ms=retry_after_ms)
                            chk = self._checkins_by_stage.get(requested_step_id) or {}
                            when = str(chk.get("when") or "").strip().lower()
                            if when in ("on_blocked", "both"):
                                ctopic = (chk.get("topic") or "").strip() or f"Check-in blocked at {requested_step_id}"
                                unread_huddles.append({"from": "scheduled", "topic": ctopic, "questions": [f"Blocked at stage {requested_step_id}: resolve failing gates and decide mode/plan changes."]})
                                self.logger.log("huddle_checkin_scheduled", stage=requested_step_id, when=when, topic=ctopic)
                            obs = {"advanced": False,
                                   "failed_gates": [
                                       {"id": g.id, "status": g.status, "evidence": g.evidence} for g in gres if g.status != "passed"
                                   ],
                                   "cooldown_active": cooldown_active,
                                   "retry_after_ms": retry_after_ms,
                                   "gates_step": step_id_for_gates}
                    elif tool_name == "write_artifact":
                        rel = tool_args.get("path") or ""
                        content = tool_args.get("content") or ""
                        tags = tool_args.get("tags") or []
                        rel_s = str(rel).replace("\\", "/")
                        if rel_s.startswith("artifacts/"):
                            rel_s = rel_s[len("artifacts/") :]
                        art = self.artifacts.add_text(rel_s, content, tags=tags, meta={"by": "router"})
                        doc_id = hashlib.sha256((art.sha256 + "|" + art.path).encode("utf-8")).hexdigest()[:16]
                        self.rag.ingest_text(doc_id, content, art.path, tags=tags, meta={"kind": "artifact_write", "by": "router"})
                        self.logger.log("rag_ingest_router", doc_id=doc_id, path=art.path)
                        obs = {"path": art.path, "hash": f"sha256:{art.sha256}", "size": len(content.encode("utf-8"))}
                    elif tool_name == "read_artifact":
                        rel = tool_args.get("path") or ""
                        rel_s = str(rel).replace("\\", "/")
                        if rel_s.startswith("artifacts/"):
                            rel_s = rel_s[len("artifacts/") :]
                        base_art = os.path.normcase(os.path.realpath(os.path.join(self.run_dir, "artifacts")))
                        abspath = os.path.normcase(os.path.realpath(os.path.join(base_art, rel_s)))
                        try:
                            within = os.path.commonpath([base_art, abspath]) == base_art
                        except ValueError:
                            within = abspath == base_art or abspath.startswith(base_art + os.sep)
                        if not within:
                            raise ValueError("path escapes artifacts root")
                        with open(abspath, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read(200_000)
                        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
                        obs = {"path": os.path.join("artifacts", rel_s).replace("\\", "/"), "content": content, "mime": "text/plain", "hash": f"sha256:{h}"}
                    elif tool_name == "write_file":
                        rel = tool_args.get("path") or ""
                        content = tool_args.get("content") or ""
                        mode = (tool_args.get("mode") or "overwrite").lower()
                        abs_path = self._resolve_workspace_path(rel)
                        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                        write_mode = "a" if mode == "append" else "w"
                        with open(abs_path, write_mode, encoding="utf-8") as f:
                            f.write(content)
                        if rel:
                            self._saw_write_file = True
                            self._record_touched(abs_path)
                        self.logger.log(
                            "tool_file_write",
                            requester="router",
                            tool_call_id=(tc.get("id") if isinstance(tc, dict) else None),
                            path=abs_path,
                            mode=mode,
                            bytes=len(content.encode("utf-8")),
                        )
                        txt = content
                        sha = hashlib.sha256(txt.encode("utf-8")).hexdigest()
                        doc_id = hashlib.sha256((sha + "|" + abs_path).encode("utf-8")).hexdigest()[:16]
                        self.rag.ingest_text(doc_id, txt, abs_path, tags=["workspace", "router"], meta={"kind": "workspace_write", "by": "router"})
                        self.logger.log("rag_ingest_router", doc_id=doc_id, path=abs_path)
                        obs = {
                            "path": abs_path,
                            "bytes": len(content.encode("utf-8")),
                            "mode": mode,
                        }
                        self._sync_checklist_state()
                        self._save_checklist()
                    elif tool_name == "read_file":
                        rel = tool_args.get("path") or ""
                        max_bytes = int(tool_args.get("max_bytes") or 200000)
                        if max_bytes < 1:
                            max_bytes = 1
                        if max_bytes > 500_000:
                            max_bytes = 500_000
                        abs_path = self._resolve_workspace_path(rel)
                        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read(max_bytes)
                        self.logger.log(
                            "tool_file_read",
                            requester="router",
                            tool_call_id=(tc.get("id") if isinstance(tc, dict) else None),
                            path=abs_path,
                            bytes=len(content.encode("utf-8")),
                            truncated=len(content) >= max_bytes,
                        )
                        obs = {"path": abs_path, "content": content, "truncated": len(content) >= max_bytes}
                    elif tool_name == "delete_file":
                        rel = tool_args.get("path") or ""
                        missing_ok = bool(tool_args.get("missing_ok")) if isinstance(tool_args, dict) else False
                        abs_path = self._resolve_workspace_path(rel)
                        if not os.path.exists(abs_path):
                            obs = {"deleted": False, "missing": True, "path": abs_path} if missing_ok else {"error": "not_found", "path": abs_path}
                        elif os.path.isdir(abs_path):
                            obs = {"error": "is_directory", "path": abs_path}
                        else:
                            try:
                                os.remove(abs_path)
                                self._record_touched(abs_path)
                                self.logger.log(
                                    "tool_file_delete",
                                    requester="router",
                                    tool_call_id=(tc.get("id") if isinstance(tc, dict) else None),
                                    path=abs_path,
                                )
                                obs = {"deleted": True, "path": abs_path}
                            except OSError as e:
                                obs = {"error": str(e), "path": abs_path}
                    elif tool_name == "run_command":
                        cmd = tool_args.get("command") or ""
                        reason = tool_args.get("reason") or ""
                        cwd = tool_args.get("cwd")
                        timeout_sec = int(tool_args.get("timeout_sec") or 120)
                        if not reason.strip():
                            raise ValueError("reason is required for run_command")
                        self.logger.log(
                            "command_request",
                            requester="router",
                            tool_call_id=(tc.get("id") if isinstance(tc, dict) else None),
                            command=cmd,
                            cwd=cwd or self.cwd,
                            reason=reason,
                        )
                        if not cwd:
                            cd_cwd, rewritten = self._rewrite_leading_cd(cmd)
                            if cd_cwd:
                                cwd = cd_cwd
                                cmd = rewritten
                        deny_reason = self._validate_command(cmd)
                        if deny_reason:
                            self.logger.log(
                                "command_blocked",
                                requester="router",
                                tool_call_id=(tc.get("id") if isinstance(tc, dict) else None),
                                command=cmd,
                                reason=deny_reason,
                            )
                            obs = {"error": deny_reason}
                        else:
                            run_cwd = self._resolve_workspace_path(cwd) if cwd else self.cwd
                            if not os.path.isdir(run_cwd):
                                obs = {"error": f"cwd does not exist: {run_cwd}"}
                                self.logger.log(
                                    "command_failed",
                                    requester="router",
                                    tool_call_id=(tc.get("id") if isinstance(tc, dict) else None),
                                    command=cmd,
                                    cwd=run_cwd,
                                    error="cwd_not_found",
                                )
                            else:
                                use_shell = self._should_use_shell(cmd)
                                env = None
                                if "pytest" in str(cmd or "").lower():
                                    env = dict(os.environ)
                                    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
                                try:
                                    args = cmd if use_shell else self._split_command_args(cmd)
                                except ValueError:
                                    use_shell = True
                                    args = cmd
                                try:
                                    proc = subprocess.run(
                                        args,
                                        shell=use_shell,
                                        cwd=run_cwd,
                                        capture_output=True,
                                        text=True,
                                        timeout=timeout_sec,
                                        env=env,
                                    )
                                except FileNotFoundError:
                                    proc = subprocess.run(
                                        self._shell_exec_args(cmd),
                                        shell=False,
                                        cwd=run_cwd,
                                        capture_output=True,
                                        text=True,
                                        timeout=timeout_sec,
                                        env=env,
                                    )
                                self.logger.log(
                                    "command_executed",
                                    requester="router",
                                    tool_call_id=(tc.get("id") if isinstance(tc, dict) else None),
                                    command=cmd,
                                    cwd=run_cwd,
                                    returncode=proc.returncode,
                                )
                                obs = {
                                    "command": cmd,
                                    "cwd": run_cwd,
                                    "returncode": proc.returncode,
                                    "stdout": proc.stdout[-4000:],
                                    "stderr": proc.stderr[-4000:],
                                }
                                cmd_l = str(cmd or "").lower()
                                if "pytest" in cmd_l or "smoke" in cmd_l:
                                    self._saw_command_test_result = True
                                    status = "passed" if proc.returncode == 0 else "failed"
                                    payload = {
                                        "id": "smoke_suite",
                                        "status": status,
                                        "metrics": {"test_type": "command", "command": cmd},
                                        "evidence": [
                                            {"message": "stdout_tail", "text": (proc.stdout or "")[-1500:]},
                                            {"message": "stderr_tail", "text": (proc.stderr or "")[-1500:]},
                                        ],
                                    }
                                    try:
                                        self.artifacts.add_text(
                                            os.path.join("contracts", "results", "smoke_suite.json"),
                                            json.dumps(payload, ensure_ascii=False, indent=2),
                                            tags=["contracts", "result"],
                                            meta={"kind": "ContractTestResult", "id": "smoke_suite", "source": "run_command"},
                                        )
                                        if "pytest" in cmd_l and ("test_api_contract.py" in cmd_l or "contract/test_api_contract.py" in cmd_l):
                                            payload2 = {
                                                "id": "api_contract",
                                                "status": status,
                                                "metrics": {"test_type": "schema", "source": "pytest", "command": cmd},
                                                "evidence": payload["evidence"],
                                            }
                                            self.artifacts.add_text(
                                                os.path.join("contracts", "results", "api_contract.json"),
                                                json.dumps(payload2, ensure_ascii=False, indent=2),
                                                tags=["contracts", "result"],
                                                meta={"kind": "ContractTestResult", "id": "api_contract", "source": "run_command"},
                                            )
                                    except OSError as e:
                                        self.logger.log("command_test_result_write_failed", error=str(e))
                    elif tool_name == "finalize_run":
                        allowed, missing = self._finalization_allowed(evaluator, self._finalization_gates(gates))
                        if not allowed:
                            obs = {"error": "finalization_blocked", "missing": missing}
                            self.logger.log("finalization_blocked", reason="guardrails", missing=missing)
                            messages.append({"role": "system", "content": "Guardrail: finalize_run blocked. Complete required steps and retry."})
                        else:
                            report = run_finalization(self.run_dir, self.artifacts, self.logger, decisions, evaluator, workspace_root=self.cwd, run_started_at=self._workspace_baseline_at)
                            deliverables = report.get("deliverables", [None])[0]
                            obs = {"deliverables": deliverables, "report": os.path.join("artifacts", "finalization", "report.json")}
                            finalized = True
                    else:
                        err = f"unknown tool: {tool_name}"
                        obs = {"error": err}
                except Exception as e:
                    err = str(e)
                    obs = {"error": err}
                _finish_tool(obs, err)

            if step_idx % 3 == 0 or finalized:
                snap = self._snapshot_state(plan_graph, evaluator, decisions, unread_huddles, tools)
                messages.append({"role": "system", "content": "State update: " + json.dumps(snap, ensure_ascii=False)})

        if not finalized:
            allowed, missing = self._finalization_allowed(evaluator, self._finalization_gates(gates))
            if allowed:
                report = run_finalization(self.run_dir, self.artifacts, self.logger, decisions, evaluator, workspace_root=self.cwd, run_started_at=self._workspace_baseline_at)
                deliverables = report.get("deliverables", [None])[0]
                self.logger.log("router_finalize_auto", reason="budget_exhausted", steps=step_idx)
            else:
                self.logger.log("finalization_blocked", reason="guardrails_auto", missing=missing)

        try:
            plan_graph.save(self.run_dir)
        except OSError as e:
            self.logger.log("plan_graph_save_failed", error=str(e))

        try:
            snap_path = os.path.join(self.run_dir, "artifacts", "plans", "snapshot.json")
            os.makedirs(os.path.dirname(snap_path), exist_ok=True)
            if not os.path.exists(snap_path):
                with open(snap_path, "w", encoding="utf-8") as f:
                    json.dump([], f)
        except OSError as e:
            self.logger.log("plan_snapshot_init_failed", error=str(e))

        try:
            transcript_md = transcript.render_markdown()
            self.artifacts.add_text(
                "transcript.md",
                transcript_md,
                tags=["transcript", "router"],
                meta={},
            )
        except OSError as e:
            self.logger.log("transcript_write_failed", error=str(e))

        summary = self._build_summary(agents or {}, evaluator, decisions)
        if self._web_disabled_by_flag:
            summary["web_search"] = "disabled_by_flag"
        final_report_rel = os.path.join("artifacts", "finalization", "report.json")
        if os.path.exists(os.path.join(self.run_dir, final_report_rel)):
            summary["finalization_report"] = final_report_rel
        else:
            summary["finalization_report"] = None
            allowed, missing = self._finalization_allowed(evaluator, self._finalization_gates(gates))
            if not allowed:
                summary["finalization_blocked"] = True
                summary["finalization_missing"] = missing
        self.artifacts.add_text("run_summary.json", json.dumps(summary, indent=2), tags=["summary"])
        self.logger.log("run_complete", summary_path=os.path.join(self.run_dir, "artifacts", "run_summary.json"))
        tp = None
        try:
            tp = generate_run_transcript(self.run_dir)
            self.logger.log("transcript_generated", path=tp)
        except OSError as e:
            self.logger.log("transcript_error", error=str(e))

        return {
            "artifact_dir": os.path.join(self.run_dir, "artifacts"),
            "log_path": self.logger.path(),
            "run_id": self.run_id,
            "workspace_dir": self.workspace_root,
            "summary_path": os.path.join(self.run_dir, "artifacts", "run_summary.json"),
            "transcript_path": tp or os.path.join(self.run_dir, "transcript.md"),
        }

    def _build_summary(self, agents: Dict[str, Any], evaluator: GateEvaluator, decisions: List[DecisionSummary]) -> Dict[str, Any]:
        evaluator.load_test_results()
        reports_dir = os.path.join(self.run_dir, "artifacts", "contracts", "results")
        reports = []
        if os.path.isdir(reports_dir):
            for n in os.listdir(reports_dir):
                if n.endswith(".json"):
                    reports.append(os.path.join("artifacts", "contracts", "results", n))
        agent_reports: Dict[str, Any] = {}
        for k, a in agents.items():
            rep = a.report()
            agent_reports[k] = asdict(rep)
        providers = (self.cfg.to_public_dict().get('providers') if self.cfg else {})
        plan_snapshot_path = os.path.join("artifacts", "plans", "snapshot.json")
        return {
            "artifacts_root": os.path.join("artifacts"),
            "contract_reports": reports,
            "test_statuses": evaluator.latest_tests,
            "decisions": [asdict(d) for d in decisions],
            "agent_reports": agent_reports,
            "providers": providers,
            "router_provider_order": self.cfg.router_provider_order if self.cfg else [],
            "agent_provider_order": self.cfg.agent_provider_order if self.cfg else [],
            "mode": self.mode,
            "plan_snapshots": plan_snapshot_path,
            "scaffolds": {"backend": os.path.join("artifacts", "backend"), "frontend": os.path.join("artifacts", "frontend")},
            "checklist": [i.to_dict() for i in self._checklist.items],
            "checklist_complete": self._checklist.is_complete(),
        }
