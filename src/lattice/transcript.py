from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def _ts() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _fmt_messages(messages: List[Dict[str, Any]], max_len: int = 800) -> str:
    parts: List[str] = []
    for m in messages or []:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, dict):
            try:
                content = json.dumps(content)
            except Exception:
                content = str(content)
        s = str(content)
        if len(s) > max_len:
            s = s[: max_len - 3] + "..."
        parts.append(f"- {role}: {s}")
    return "\n".join(parts)


def _fmt_str(s: Optional[str], max_len: int = 1200) -> str:
    if not s:
        return ""
    s = str(s)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


@dataclass
class TranscriptEntry:
    kind: str
    title: str
    body: str
    ts: str = field(default_factory=_ts)


class RunningTranscript:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.entries: List[TranscriptEntry] = []

    def add_model_call(
        self,
        title: str,
        provider: str,
        model: str,
        messages: List[Dict[str, Any]],
        output: Optional[str],
        tools_offered: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        error: Optional[str] = None,
    ) -> None:
        tool_names = []
        if tools_offered:
            for t in tools_offered:
                fn = (t or {}).get("function", {})
                n = fn.get("name")
                if n:
                    tool_names.append(n)
        called_list = []
        if tool_calls:
            for tc in tool_calls:
                fn = (tc or {}).get("function", {})
                n = fn.get("name")
                args = fn.get("arguments")
                called_list.append(f"{n}({args})")

        body_lines: List[str] = []
        body_lines.append(f"Provider: {provider}")
        body_lines.append(f"Model: {model}")
        if tool_names:
            body_lines.append(f"Tools Offered: {', '.join(tool_names)}")
        if tool_choice is not None:
            body_lines.append(f"Tool Choice: {tool_choice}")
        if called_list:
            body_lines.append(f"Tools Called: {', '.join(called_list)}")
        if error:
            body_lines.append(f"Error: {error}")
        body_lines.append("Input Messages:")
        body_lines.append(_fmt_messages(messages))
        if output is not None:
            body_lines.append("")
            body_lines.append("Output:")
            body_lines.append(_fmt_str(output))

        self.entries.append(TranscriptEntry(kind="model", title=title, body="\n".join(body_lines)))

    def add_meeting(self, topic: str, attendees: List[str], questions: List[str]) -> None:
        body = [f"Attendees: {', '.join(attendees)}"]
        if questions:
            body.append("Questions:")
            for q in questions:
                body.append(f"- {q}")
        self.entries.append(TranscriptEntry(kind="meeting", title=f"Huddle Called — {topic}", body="\n".join(body)))

    def add_meeting_notes(self, topic: str, notes_text: str) -> None:
        self.entries.append(
            TranscriptEntry(kind="meeting-notes", title=f"Huddle Notes — {topic}", body=_fmt_str(notes_text, 2000))
        )

    def add_decision_injection(self, text: str) -> None:
        self.entries.append(TranscriptEntry(kind="decision", title="Decision Injected", body=_fmt_str(text, 2000)))

    def add_info(self, title: str, body: str) -> None:
        self.entries.append(TranscriptEntry(kind="info", title=title, body=body))

    def render_markdown(self) -> str:
        parts: List[str] = []
        parts.append(f"# Run Transcript — {self.run_id}")
        for e in self.entries:
            parts.append("")
            parts.append(f"## {e.title}")
            parts.append(f"- Time: {e.ts}")
            parts.append("")
            parts.append(e.body)
        return "\n".join(parts) + "\n"
