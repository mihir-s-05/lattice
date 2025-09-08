import json
import os
import random
import string
from datetime import datetime
import hashlib
import glob
from typing import Dict, Optional, List, Any

from .artifacts import ArtifactStore
from .config import RunConfig, load_run_config
from .providers import call_with_fallback, ProviderError
from .rag import RagIndex
from .runlog import RunLogger
from .secrets import redact_secrets
from .huddle import (
    parse_decision_summaries,
    save_decisions,
    save_huddle,
    decision_injection_text,
)
from .transcript import RunningTranscript
from .constants import (
    DEFAULT_RAG_MIN_SCORE,
    DEFAULT_RAG_MAX_INGEST_FILES,
    DEFAULT_RAG_MAX_FILE_SIZE,
    RAG_INGEST_PATTERNS
)


def gen_run_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"run-{ts}-{suffix}"


class WorkerRunner:
    def __init__(self, cwd: str, run_id: Optional[str] = None) -> None:
        self.cwd = cwd
        self.run_id = run_id or gen_run_id()
        self.run_dir = os.path.join(cwd, "runs", self.run_id)
        os.makedirs(self.run_dir, exist_ok=True)
        self.logger = RunLogger(self.run_dir)
        self.artifacts = ArtifactStore(self.run_dir)
        self.rag_index = RagIndex(self.run_dir)
        self.cfg: Optional[RunConfig] = None

    def _huddle_tool_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "huddle.request",
                    "description": "Request a huddle with Router and a Worker to decide interfaces/contracts and produce DecisionSummary JSON.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "description": "Huddle topic, e.g., 'API shape for Notes'"},
                            "questions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Key questions to resolve",
                            },
                            "proposed_contract": {
                                "type": "string",
                                "description": "Optional proposed contract/spec to review",
                            },
                        },
                        "required": ["topic"],
                    },
                },
            }
        ]

    def _execute_huddle(
        self,
        topic: str,
        questions: List[str],
        proposed_contract: Optional[str],
        transcript: Optional[RunningTranscript] = None,
    ) -> Dict[str, Any]:
        assert self.cfg is not None
        attendees = ["router", "worker:default"]
        self.logger.log(
            "huddle_request",
            requester="router",
            attendees=attendees,
            topic=topic,
            questions=questions,
            proposed_contract=(proposed_contract[:5000] if proposed_contract else None),
        )

        if transcript is not None:
            transcript.add_meeting(topic=topic, attendees=attendees, questions=questions or [])

        worker_sys = (
            "You are a Worker in a Huddle with a Router. "
            "Reply ONCE with clarifications and a concrete proposal for the interface/contract. "
            "Do NOT output DecisionSummary JSON. Focus on endpoints, resource schema, tradeoffs, and a brief proposed contract excerpt if applicable."
        )
        agenda_lines = [f"Huddle Agenda — {topic}"]
        if questions:
            agenda_lines.append("Questions:")
            for q in questions:
                agenda_lines.append(f"- {q}")
        if proposed_contract:
            agenda_lines.append("\nProposed contract (optional):\n" + proposed_contract)
        worker_messages = [
            {"role": "system", "content": worker_sys},
            {"role": "user", "content": "\n".join(agenda_lines)},
        ]

        try:
            w_provider, w_base, w_model, w_raw, w_attempts = call_with_fallback(
                providers=self.cfg.providers,
                order=self.cfg.router_provider_order,
                messages=worker_messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
            )
        except ProviderError as e:
            self.logger.log("huddle_error", error=str(e))
            raise

        worker_text = ""
        try:
            worker_text = w_raw["choices"][0]["message"].get("content") or ""
        except Exception:
            worker_text = str(w_raw)

        if transcript is not None:
            transcript.add_model_call(
                title="Huddle Worker",
                provider=w_provider,
                model=w_model,
                messages=worker_messages,
                output=worker_text,
            )

        router_sys = (
            "You are the Router closing this huddle. "
            "Return 1-3 DecisionSummary JSON objects ONLY (no prose, no markdown). "
            "Fields: id (optional, use ds_<ulid> if you assign one), topic, options, decision, rationale, risks, "
            "actions (owner, task), contracts (name, schema_hash), links (artifact)."
        )
        router_close_user = (
            f"Topic: {topic}\n\n"
            + ("Questions:\n" + "\n".join(f"- {q}" for q in (questions or [])) + "\n\n" if questions else "")
            + "Worker Reply:\n"
            + worker_text
        )
        router_close_messages = [
            {"role": "system", "content": router_sys},
            {"role": "user", "content": router_close_user},
        ]

        try:
            r_provider, r_base, r_model, r_raw, r_attempts = call_with_fallback(
                providers=self.cfg.providers,
                order=self.cfg.router_provider_order,
                messages=router_close_messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
                tool_choice="none",
            )
        except ProviderError as e:
            self.logger.log("huddle_error", error=str(e))
            raise

        router_text = ""
        try:
            router_text = r_raw["choices"][0]["message"].get("content") or ""
        except Exception:
            router_text = str(r_raw)

        if transcript is not None:
            transcript.add_model_call(
                title="Huddle Router Close",
                provider=r_provider,
                model=r_model,
                messages=router_close_messages,
                output=router_text,
                tool_choice="none",
            )

        decisions = parse_decision_summaries(router_text)
        saved = save_decisions(self.run_dir, self.artifacts, self.rag_index, decisions)
        for d, rel in saved:
            self.logger.log(
                "decision_summary",
                decision_id=d.id,
                topic=d.topic,
                decision=d.decision,
                path=os.path.join(self.run_dir, rel),
            )

        combined_notes = (
            "Router Agenda:\n" + "\n".join(agenda_lines) + "\n\n" + "Worker Reply:\n" + worker_text
        )
        rec, transcript_rel, record_rel = save_huddle(
            run_dir=self.run_dir,
            artifacts=self.artifacts,
            rag_index=self.rag_index,
            requester="router",
            attendees=attendees,
            topic=topic,
            questions=questions or [],
            notes=combined_notes,
            decisions=decisions,
        )
        self.logger.log(
            "huddle_complete",
            huddle_id=rec.id,
            attendees=attendees,
            transcript_path=os.path.join(self.run_dir, transcript_rel),
            decisions=[d.id for d in decisions],
        )

        return {
            "record": rec,
            "decisions": decisions,
            "transcript_path": transcript_rel,
            "notes": combined_notes,
            "worker_provider": w_provider,
            "worker_model": w_model,
            "worker_messages": worker_messages,
            "worker_raw": w_raw,
            "router_close_provider": r_provider,
            "router_close_model": r_model,
            "router_close_messages": router_close_messages,
            "router_close_raw": r_raw,
        }

    def _snapshot_env(self) -> Dict[str, str]:
        keys = [
            "LATTICE_PROVIDER_ORDER",
            "LATTICE_PROVIDER",
            "LATTICE_MODEL",
            "LATTICE_BASE_URL",
            "LATTICE_USE_RAG",
            "LATTICE_TEMPERATURE",
            "LATTICE_MAX_TOKENS",
            "GROQ_BASE_URL",
            "GROQ_API_KEY",
            "GROQ_MODEL",
            "GEMINI_BASE_URL",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "LMSTUDIO_BASE_URL",
            "LMSTUDIO_API_KEY",
            "LMSTUDIO_MODEL",
        ]
        snap = {}
        for k in keys:
            v = os.environ.get(k)
            if v is not None:
                if any(tok in k for tok in ["KEY", "TOKEN", "SECRET", "PASSWORD"]):
                    snap[k] = "REDACTED"
                else:
                    snap[k] = v
        return redact_secrets(snap)

    def run(self, prompt: str, use_rag: Optional[bool] = None) -> Dict[str, str]:
        self.cfg = load_run_config(self.run_id, prompt)
        if use_rag is not None:
            self.cfg.use_rag = use_rag

        cfg_public = self.cfg.to_public_dict()
        with open(os.path.join(self.run_dir, "config.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(cfg_public, indent=2))

        self.logger.log(
            "run_start",
            run_id=self.run_id,
            run_dir=self.run_dir,
            config=cfg_public,
            env=self._snapshot_env(),
        )

        if self.cfg.use_rag:
            self._pre_ingest_repo_files()

        rag_used = False
        rag_queries = []
        rag_hits = []
        context_text = ""
        if self.cfg.use_rag:
            q = prompt
            rag_queries.append(q)
            hits = self.rag_index.search(q, top_k=3)
            try:
                min_score = float(os.environ.get("LATTICE_RAG_MIN_SCORE", str(DEFAULT_RAG_MIN_SCORE)))
            except Exception:
                min_score = DEFAULT_RAG_MIN_SCORE
            filtered_hits = [h for h in (hits or []) if float(h.get("score", 0.0)) >= min_score]
            rag_hits = filtered_hits
            ctx_parts = []
            if filtered_hits:
                rag_used = True
                for h in filtered_hits:
                    path = h.get("path")
                    snippet = h.get("snippet")
                    if snippet:
                        ctx_parts.append(f"From {path}:\n{snippet}")

            if not ctx_parts and any(tok in q.lower() for tok in ["readme", "repo readme"]):
                discovered = []
                candidates = [
                    "README.md",
                    "README",
                    "README.txt",
                    "Readme.md",
                    "readme.md",
                    os.path.join("docs", "README.md"),
                ]
                for rel in candidates:
                    abs_p = os.path.join(self.cwd, rel)
                    if os.path.isfile(abs_p):
                        discovered.append((rel, abs_p))
                if not discovered:
                    try:
                        for name in os.listdir(self.cwd):
                            if name.lower().startswith("readme") and os.path.isfile(os.path.join(self.cwd, name)):
                                rel = name
                                abs_p = os.path.join(self.cwd, name)
                                discovered.append((rel, abs_p))
                    except Exception:
                        pass

                if discovered:
                    rag_used = True
                    for rel, abs_p in discovered[:3]:
                        try:
                            with open(abs_p, "r", encoding="utf-8") as f:
                                content = f.read(2000)
                        except Exception:
                            continue
                        ctx_parts.append(f"From {rel}:\n{content}")
                        try:
                            did = hashlib.sha256((rel + abs_p).encode("utf-8")).hexdigest()[:16]
                            self.rag_index.ingest_text(did, content, path=abs_p)
                            rag_hits.append({
                                "doc_id": did,
                                "score": 1.0,
                                "path": abs_p,
                                "snippet": content[:300],
                            })
                        except Exception:
                            pass

            if ctx_parts:
                context_text = ("\n\n".join(ctx_parts))[:1500]

        transcript = RunningTranscript(self.run_id)

        router_messages = []
        system_preamble = (
            "You are the LATTICE Router. Be decisive and orchestrate work. "
            "If the task implies interface or API design, call the 'huddle.request' tool to pin it."
        )
        router_messages.append({"role": "system", "content": system_preamble})
        if context_text:
            router_messages.append({
                "role": "system",
                "content": f"Context from prior artifacts (may be partial):\n{context_text}",
            })
        router_messages.append({"role": "user", "content": prompt})

        tool_schema = self._huddle_tool_schema()
        hud_topic: Optional[str] = None
        hud_questions: List[str] = []
        hud_contract: Optional[str] = None
        try:
            provider_name_1, base_url_1, model_1, raw_1, attempts_1 = call_with_fallback(
                providers=self.cfg.providers,
                order=self.cfg.router_provider_order,
                messages=router_messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
                tools=tool_schema,
                tool_choice="auto",
            )
        except ProviderError as e:
            self.logger.log(
                "run_error",
                error=str(e),
                rag_used=rag_used,
                rag_queries=rag_queries,
                rag_hits=rag_hits,
            )
            raise

        tool_calls = []
        try:
            tool_calls = raw_1["choices"][0]["message"].get("tool_calls") or []
        except Exception:
            tool_calls = []
        pre_out = ""
        try:
            pre_out = raw_1["choices"][0]["message"].get("content") or ""
        except Exception:
            pre_out = ""
        transcript.add_model_call(
            title="Router (pre-huddle)",
            provider=provider_name_1,
            model=model_1,
            messages=router_messages,
            output=pre_out,
            tools_offered=tool_schema,
            tool_choice="auto",
            tool_calls=tool_calls,
        )
        if tool_calls:
            for tc in tool_calls:
                try:
                    fn = tc.get("function", {})
                    name = fn.get("name")
                    if name == "huddle.request":
                        args_raw = fn.get("arguments")
                        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                        hud_topic = args.get("topic")
                        hud_questions = args.get("questions") or []
                        hud_contract = args.get("proposed_contract")
                        break
                except Exception:
                    continue

        if not hud_topic and any(tok in prompt.lower() for tok in ["api", "rest", "grpc", "interface"]):
            hud_topic = "Interface/API design"
            hud_questions = ["What endpoints and payloads?", "What canonical resource schema?"]

        decisions = []
        injected_into = []
        if hud_topic:
            hres = self._execute_huddle(hud_topic, hud_questions or [], hud_contract, transcript)
            decisions = hres["decisions"]
            inj = decision_injection_text(decisions)
            injected_into = ["router:post-huddle"]
            self.logger.log(
                "decision_injection",
                injected_contexts=injected_into,
                summary_text=inj,
                decisions=[d.id for d in decisions],
            )
            transcript.add_decision_injection(inj)
            final_messages = []
            post_huddle_preamble = (
                "You are the LATTICE Router. Be decisive and orchestrate work. "
                "A huddle decision has been made and tools are now disabled. "
                "Do not call any tools; produce the final answer based on the DecisionSummary."
            )
            final_messages.append({"role": "system", "content": post_huddle_preamble})
            final_messages.append({"role": "system", "content": f"DecisionSummary:\n{inj}"})
            if context_text:
                final_messages.append({
                    "role": "system",
                    "content": f"Context from prior artifacts (may be partial):\n{context_text}",
                })
            final_messages.append({"role": "user", "content": prompt})
        else:
            final_messages = router_messages

        try:
            provider_name, base_url, model, raw, attempts = call_with_fallback(
                providers=self.cfg.providers,
                order=self.cfg.router_provider_order,
                messages=final_messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
                tool_choice="none",
            )
        except ProviderError as e:
            msg = str(e)
            if "tool" in msg.lower() and ("tool" in msg.lower() and "call" in msg.lower()):
                guarded = list(final_messages)
                guarded.insert(0, {"role": "system", "content": "Tools are disabled. Do not call any tools. Return plain text only."})
                provider_name, base_url, model, raw, attempts = call_with_fallback(
                    providers=self.cfg.providers,
                    order=self.cfg.router_provider_order,
                    messages=guarded,
                    temperature=self.cfg.temperature,
                    max_tokens=self.cfg.max_tokens,
                    logger=self.logger,
                    tool_choice="none",
                )
                transcript.add_info(
                    title="Post-huddle model retry",
                    body="Initial post-huddle call attempted a tool; retried with tools disabled.",
                )
            else:
                self.logger.log(
                    "run_error",
                    error=str(e),
                    rag_used=rag_used,
                    rag_queries=rag_queries,
                    rag_hits=rag_hits,
                )
                raise

        text = ""
        try:
            text = raw["choices"][0]["message"].get("content") or ""
        except Exception:
            text = str(raw)

        final_tool_calls = []
        try:
            final_tool_calls = raw["choices"][0]["message"].get("tool_calls") or []
        except Exception:
            final_tool_calls = []
        transcript.add_model_call(
            title=("Router (post-huddle)" if hud_topic else "Router (final)"),
            provider=provider_name,
            model=model,
            messages=final_messages,
            output=text,
            tools_offered=None,
            tool_choice=("none" if hud_topic else None),
            tool_calls=final_tool_calls,
        )

        artifact_name = "output.txt"
        art = self.artifacts.add_text(
            artifact_name,
            text,
            tags=["output", "llm"],
            meta={
                "provider": provider_name,
                "model": model,
                "injected_decisions": [getattr(d, "id", None) for d in decisions] if decisions else [],
            },
        )
        try:
            self.rag_index.ingest_text(art.id, text, art.path)
        except Exception as e:
            self.logger.log("rag_error", error=str(e))

        self.logger.log(
            "run_complete",
            artifact_path=os.path.join(self.run_dir, art.path),
            log_path=self.logger.path(),
            rag_used=rag_used,
            rag_queries=rag_queries,
            rag_hits=rag_hits,
            rag_min_score=os.environ.get("LATTICE_RAG_MIN_SCORE", "0.15"),
            injected_contexts=injected_into,
            decisions=[getattr(d, "id", None) for d in decisions] if decisions else [],
        )

        try:
            transcript_md = transcript.render_markdown()
            self.artifacts.add_text(
                "transcript.md",
                transcript_md,
                tags=["transcript", "llm"],
                meta={
                    "injected_decisions": [getattr(d, "id", None) for d in decisions] if decisions else [],
                },
            )
        except Exception as e:
            self.logger.log("transcript_error", error=str(e))

        return {
            "artifact_path": os.path.join(self.run_dir, art.path),
            "log_path": self.logger.path(),
            "run_id": self.run_id,
        }

    def _pre_ingest_repo_files(self) -> None:
        patterns = RAG_INGEST_PATTERNS
        max_files = int(os.environ.get("LATTICE_RAG_MAX_INGEST", str(DEFAULT_RAG_MAX_INGEST_FILES)))
        candidates: List[str] = []
        for pat in patterns:
            for p in glob.glob(os.path.join(self.cwd, pat), recursive=True):
                if os.path.isfile(p):
                    candidates.append(p)
        seen = set()
        unique: List[str] = []
        for p in candidates:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        for path in unique[:max_files]:
            try:
                with open(path, "rb") as f:
                    raw = f.read(DEFAULT_RAG_MAX_FILE_SIZE)
                digest = hashlib.sha256(raw + path.encode("utf-8")).hexdigest()
                doc_id = digest[:16]
            except Exception as e:
                self.logger.log("rag_ingest_error", path=path, error=str(e))
                continue
            try:
                self.rag_index.ingest_file(path, doc_id)
                self.logger.log("rag_ingest", path=path, doc_id=doc_id, bytes=min(len(raw), DEFAULT_RAG_MAX_FILE_SIZE))
            except Exception as e:
                self.logger.log("rag_ingest_error", path=path, error=str(e))
