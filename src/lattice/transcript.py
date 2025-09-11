from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from .constants import DEFAULT_MESSAGE_MAX_LENGTH, DEFAULT_CONTENT_MAX_LENGTH


def _ts() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _fmt_messages(messages: List[Dict[str, Any]], max_len: int = DEFAULT_MESSAGE_MAX_LENGTH) -> str:
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


def _fmt_str(s: Optional[str], max_len: int = DEFAULT_CONTENT_MAX_LENGTH) -> str:
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



def _safe_read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return None


def _infer_role_from_system(system_text: str) -> Optional[str]:
    txt = system_text or ""
    if "Router LLM" in txt:
        return "router"
    m = re.search(r"You are the\s+([A-Za-z0-9_-]+)Agent", txt)
    if m:
        return f"agent:{m.group(1).lower()}"
    if "FrontendAgent" in txt:
        return "agent:frontend"
    if "BackendAgent" in txt:
        return "agent:backend"
    if "TestsAgent" in txt or "Test" in txt:
        return "agent:tests"
    if "LlmapiAgent" in txt or "llmapi" in txt.lower():
        return "agent:llmapi"
    return None


def _write_block(fh, ts: str, model: str, role: str, event_type: str, content: Optional[str], lang_hint: Optional[str] = None) -> None:
    if content is None:
        return
    text = str(content).strip()
    if not text:
        return
    fh.write(f"## {ts} | {role}\n\n")
    fh.write(f"- Model: {model}\n")
    fh.write(f"- Event: {event_type}\n\n")
    fh.write("Output:\n\n")
    if "```" in text:
        fh.write(text + "\n\n")
    else:
        fence = "```" + (lang_hint or "")
        fh.write(f"{fence}\n{text}\n```\n\n")


def generate_run_transcript(run_dir: str, out_filename: str = "transcript.md") -> str:
    """Parse the run.jsonl in run_dir and write a formatted transcript.

    Returns the absolute path to the written transcript file.
    """
    log_path = os.path.join(run_dir, "run.jsonl")
    cfg_path = os.path.join(run_dir, "config.json")
    out_path = os.path.join(run_dir, out_filename)

    router_model_default = "unknown"
    agent_model_default = "unknown"
    try:
        with open(cfg_path, "r", encoding="utf-8") as cf:
            cfg = json.load(cf)
            router_model_default = cfg.get("router_model_default") or router_model_default
            agent_model_default = cfg.get("agent_model_default") or agent_model_default
    except Exception:
        pass

    decision_paths: Dict[str, str] = {}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("event") == "decision_summary":
                    did = rec.get("decision_id")
                    path = rec.get("path")
                    if did and path:
                        decision_paths[did] = path
    except FileNotFoundError:
        with open(out_path, "w", encoding="utf-8") as out:
            out.write(f"# Run Transcript: {os.path.basename(run_dir)}\n\n(no log found)\n")
        return out_path

    with open(out_path, "w", encoding="utf-8") as out, open(log_path, "r", encoding="utf-8", errors="replace") as f:
        out.write(f"# Run Transcript: {os.path.basename(run_dir)}\n\n")
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get("ts", "")
            ev = rec.get("event", "")

            if ev == "router_llm_turn":
                model = rec.get("model") or router_model_default
                _write_block(out, ts, model, "router", "router_llm_turn", rec.get("response_text"))
                continue

            if ev == "huddle_open":
                topic = rec.get("topic", "")
                attendees = rec.get("attendees", [])
                agenda = rec.get("agenda", rec.get("mode", ""))
                content = f"Huddle opened\nTopic: {topic}\nAgenda: {agenda}\nAttendees: {', '.join(attendees)}"
                _write_block(out, ts, router_model_default, "router", "huddle_open", content)
                continue
            if ev == "huddle_close":
                content = f"Huddle closed\nHuddle ID: {rec.get('huddle_id')}\nDuration (ms): {rec.get('duration_ms')}\nMessages: {rec.get('message_count')}"
                _write_block(out, ts, router_model_default, "router", "huddle_close", content)
                continue
            if ev == "huddle_decision":
                did = rec.get("decision_summary_id")
                lines: List[str] = [
                    "Huddle decision finalized",
                    f"Huddle ID: {rec.get('huddle_id')}",
                    f"DecisionSummary ID: {did}",
                ]
                path = decision_paths.get(did)
                if path:
                    js = _safe_read(path)
                    if js:
                        lines.append("DecisionSummary JSON:")
                        lines.append(js)
                _write_block(out, ts, router_model_default, "router", "huddle_decision", "\n".join(lines))
                continue

            if ev == "router_tool_call" and rec.get("tool_name") == "record_decision_summary":
                params = rec.get("params") or {}
                obs = rec.get("observation") or {}
                chunks: List[str] = []
                if params.get("topic"): chunks.append(f"Topic: {params['topic']}")
                if params.get("rationale"): chunks.append(f"Rationale: {params['rationale']}")
                for key in ["options","risks","actions","contracts","links","sources"]:
                    arr = params.get(key) or []
                    if arr:
                        chunks.append(f"{key.capitalize()}:")
                        for it in arr:
                            try:
                                chunks.append(f"- {json.dumps(it, ensure_ascii=False)}")
                            except Exception:
                                chunks.append(f"- {it}")
                if obs:
                    chunks.append("Observation:")
                    for k,v in obs.items():
                        chunks.append(f"- {k}: {v}")
                _write_block(out, ts, router_model_default, "router", "decision_summary", "\n".join(chunks))
                continue

            if ev == "decision_summary":
                did = rec.get("decision_id")
                topic = rec.get("topic", "")
                path = rec.get("path")
                content: List[str] = [f"DecisionSummary Event", f"ID: {did}", f"Topic: {topic}", f"Decision: {rec.get('decision')}"]
                if path:
                    js = _safe_read(path)
                    if js:
                        content.append("JSON:")
                        content.append(js)
                _write_block(out, ts, router_model_default, "router", "decision_summary", "\n".join(content), lang_hint="markdown")
                continue

            if ev == "model_call":
                messages = rec.get("messages") or []
                sys_text = ""
                for m in messages:
                    if isinstance(m, dict) and m.get("role") == "system":
                        sys_text = m.get("content") or ""
                        break
                role = _infer_role_from_system(str(sys_text)) or "agent"
                out_text = rec.get("output")
                if out_text:
                    _write_block(out, ts, rec.get("model") or (router_model_default if role == "router" else agent_model_default), role, "model_output", out_text)
                continue

            if ev == "agent_model_turn":
                agent = rec.get("agent")
                role = f"agent:{agent}" if agent and not str(agent).startswith("agent:") else (agent or "agent")
                model = rec.get("model") or agent_model_default
                prev = rec.get("output_preview")
                if prev:
                    _write_block(out, ts, model, role, "agent_model_turn", prev, lang_hint="markdown")
                continue

            if ev == "pre_finalization_validation":
                _write_block(out, ts, router_model_default, "router", "pre_finalization_validation", json.dumps(rec, ensure_ascii=False, indent=2), lang_hint="json")
                continue

            if ev == "run_complete":
                summary_path = rec.get("summary_path")
                summary = _safe_read(summary_path) if summary_path else None
                lines = ["Run complete.", f"Summary path: {summary_path or 'N/A'}"]
                if summary:
                    lines.append("Run Summary JSON:")
                    lines.append(summary)
                _write_block(out, ts, router_model_default, "router", "run_complete", "\n".join(lines))
                continue

    return out_path