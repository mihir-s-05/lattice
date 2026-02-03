import os
import json


def test_save_huddle_writes_transcript_and_summary(tmp_run_dir):
    from lattice.artifacts import ArtifactStore
    from lattice.rag import RagIndex
    from lattice.huddle import save_huddle, DecisionSummary

    store = ArtifactStore(tmp_run_dir)
    rag = RagIndex(tmp_run_dir)

    decisions = [
        DecisionSummary(id="ds_1", topic="API", decision="Use REST", rationale="Simple", sources=[{"type": "external", "url": "https://example.com"}])
    ]

    rec, transcript_rel, record_rel = save_huddle(
        run_dir=tmp_run_dir,
        artifacts=store,
        rag_index=rag,
        requester="router",
        attendees=["router", "agent:backend"],
        topic="Frobnicate API",
        questions=["How should frobnicate work?"],
        notes="frobnicate must be idempotent",
        decisions=decisions,
        mode="dialog",
        messages=[{"ts": "t", "from": "router", "content": "frobnicate should be idempotent"}],
    )

    assert rec.id
    assert transcript_rel == rec.transcript_path
    assert record_rel.endswith(f"{rec.id}.json")
    assert rec.summary_path is not None

    assert os.path.exists(os.path.join(tmp_run_dir, transcript_rel))
    assert os.path.exists(os.path.join(tmp_run_dir, rec.summary_path))
    assert os.path.exists(os.path.join(tmp_run_dir, record_rel))

    obj = json.loads(open(os.path.join(tmp_run_dir, record_rel), "r", encoding="utf-8").read())
    assert obj.get("summary_path") == rec.summary_path

    assert rec.id in rag.docs
    assert f"{rec.id}_summary" in rag.docs

    hits = rag.search("frobnicate", top_k=10)
    assert any(h.get("doc_id") in (rec.id, f"{rec.id}_summary") for h in hits)

    sem_hits = rag.search_semantic("frobnicate", top_k=10)
    assert any(h.get("doc_id") in (rec.id, f"{rec.id}_summary") for h in sem_hits)


def test_mode_switch_from_decision_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICE_RUNS_DIR", str(tmp_path / "runs"))
    from lattice.router_main import RouterRunner
    from lattice.huddle import DecisionSummary

    rr = RouterRunner(cwd=str(tmp_path), mode="ladder")
    assert rr.mode == "ladder"

    upd = rr._apply_plan_updates_from_decisions([
        DecisionSummary(id="ds_1", topic="Mode update", decision="Switch to tracks", meta={"mode": "tracks", "stage_order": ["contracts", "smoke_tests"]}),
    ])
    assert upd["mode"] == "tracks"
    assert rr.mode == "tracks"
    assert upd["stage_order"] == ["contracts", "smoke_tests"]


def test_agent_huddle_request_drain(tmp_run_dir, mock_run_config, mock_logger):
    from lattice.artifacts import ArtifactStore
    from lattice.rag import RagIndex
    from lattice.agents import BaseAgent

    class DummyAgent(BaseAgent):
        def plan(self, step_or_goal, context):
            raise NotImplementedError

        def act(self, inputs):
            return []

    store = ArtifactStore(tmp_run_dir)
    rag = RagIndex(tmp_run_dir)
    ag = DummyAgent("dummy", mock_run_config, mock_logger, store, rag, workspace_root=os.getcwd())

    ag.request_huddle("Need alignment", ["Q1"], urgency="high")
    reqs = ag.drain_huddle_requests()
    assert len(reqs) == 1
    assert reqs[0]["topic"] == "Need alignment"
    assert reqs[0]["urgency"] == "high"
    assert ag.drain_huddle_requests() == []
