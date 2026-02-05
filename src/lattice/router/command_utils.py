from __future__ import annotations

import os
import shlex
import shutil
from typing import Callable, List, Optional, Tuple

from ..command_validation import validate_command
from ..config import RunConfig


def should_use_shell(cmd: str) -> bool:
    text = (cmd or "").strip()
    if not text:
        return True
    meta = ["|", "&", ";", "<", ">", "$", "`"]
    if any(ch in text for ch in meta):
        return True
    head = text.split()[0].lower()
    if os.name == "nt" and head in ("dir", "copy", "type", "del", "ren", "move", "cls", "echo", "set", "cd", "start", "call"):
        return True
    return False


def split_command_args(cmd: str) -> List[str]:
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


def rewrite_leading_cd(cmd: str, *, resolve_path: Callable[[str], str]) -> Tuple[Optional[str], str]:
    text = (cmd or "").strip()
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
            parts = split_command_args(prefix)
        except ValueError:
            continue
        if len(parts) < 2 or parts[0].lower() != "cd":
            continue
        rel = parts[1]
        try:
            abs_cwd = resolve_path(rel)
        except ValueError:
            continue
        return (abs_cwd, rest)
    return (None, cmd)


def shell_exec_args(cmd: str) -> List[str]:
    if os.name == "nt":
        ps = shutil.which("pwsh") or shutil.which("powershell")
        if ps:
            return [ps, "-NoProfile", "-NonInteractive", "-Command", cmd]
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/c", cmd]
    sh = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
    return [sh, "-lc", cmd]


def validate_command_for_run(cmd: str, cfg: Optional[RunConfig]) -> Optional[str]:
    policy = getattr(cfg, "command_policy", None) if cfg else None
    allowlist = list((policy.allowlist if policy else []) or [])
    denylist = list((policy.denylist if policy else []) or [])
    return validate_command(cmd, allowlist=allowlist, denylist=denylist)

