from __future__ import annotations

import os
import re
from typing import Iterable, Optional, Sequence, Dict


DANGEROUS_PATTERNS: Sequence[str] = [
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
    r"\brd\s+/s\s+/q\b",
    r"\bremove-item\b.*-recurse.*-force",
    r"\bcmd\s+/c\s+rd\b",
    r"\bcmd\s+/c\s+del\b",
    r"\brimraf\b\s+[/\\]",
]

BLOCKED_NETWORK_TOKENS: Sequence[str] = [
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

DEFAULT_SAFE_COMMANDS: frozenset[str] = frozenset({
    "python",
    "py",
    "pytest",
    "pip",
    "pip3",
    "uv",
    "poetry",
    "npm",
    "node",
    "npx",
    "pnpm",
    "yarn",
    "git",
    "rg",
    "ruff",
    "black",
    "mypy",
    "echo",
    "dir",
    "type",
    "cat",
})

DEFAULT_MESSAGES: Dict[str, str] = {
    "required": "command is required",
    "dangerous": "command blocked: dangerous pattern detected",
    "network": "command blocked: network-capable tooling is disabled by default",
    "denylist": "command blocked by denylist entry: {entry}",
    "allowlist": "command blocked: not in allowlist",
    "default_allow": "command blocked: not in default safe allowlist (head={head}). Configure allowlist to permit.",
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format_message(msg: str, **kwargs: str) -> str:
    return msg.format_map(_SafeDict(kwargs))


def command_is_dangerous(cmd: str, *, patterns: Sequence[str] = DANGEROUS_PATTERNS) -> bool:
    if not isinstance(cmd, str):
        return False
    text = cmd.lower()
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def validate_command(
    cmd: str,
    *,
    allowlist: Optional[Sequence[str]] = None,
    denylist: Optional[Sequence[str]] = None,
    default_allowlist: Optional[Sequence[str]] = None,
    blocked_tokens: Optional[Sequence[str]] = BLOCKED_NETWORK_TOKENS,
    require_command: bool = False,
    allowlist_prefix_match: bool = True,
    messages: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    msgs = dict(DEFAULT_MESSAGES)
    if messages:
        msgs.update(messages)

    if require_command and (not isinstance(cmd, str) or not cmd.strip()):
        return msgs["required"]

    if command_is_dangerous(cmd):
        return msgs["dangerous"]

    lowered = (cmd or "").lower()
    if blocked_tokens and any(t in lowered for t in blocked_tokens):
        return msgs["network"]

    deny_entries = [d for d in (denylist or []) if str(d).strip()]
    for d in deny_entries:
        if str(d).lower() in lowered:
            return _format_message(msgs["denylist"], entry=d)

    allow_entries = [a for a in (allowlist or []) if str(a).strip()]

    s = str(cmd or "").strip()
    if s.startswith(("'", '"')) and len(s) > 1:
        q = s[0]
        end = s.find(q, 1)
        head = s[1:end] if end > 1 else s[1:]
    else:
        head = s.split()[0] if s else ""
    head_l = head.lower()

    if allow_entries:
        if allowlist_prefix_match:
            ok = any(head_l.startswith(str(a).lower()) for a in allow_entries)
        else:
            ok = head_l in {str(a).lower() for a in allow_entries}
        if not ok:
            return msgs["allowlist"]
    else:
        defaults = {str(x).lower() for x in (default_allowlist or DEFAULT_SAFE_COMMANDS)}
        if head_l and head_l not in defaults:
            return _format_message(msgs["default_allow"], head=head_l)
    return None
