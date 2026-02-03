import json
import os
from concurrent.futures import ThreadPoolExecutor


def test_threaded_writes_do_not_corrupt_artifact_store_rag_or_logs(tmp_path):
    from lattice.artifacts import ArtifactStore
    from lattice.rag import RagIndex
    from lattice.runlog import RunLogger

    run_dir = str(tmp_path / "run")
    os.makedirs(run_dir, exist_ok=True)

    store = ArtifactStore(run_dir)
    rag = RagIndex(run_dir)
    logger = RunLogger(run_dir)

    def worker(i: int) -> None:
        store.add_text(f"concurrent/{i}.txt", f"hello {i}", tags=["t"], meta={"i": i})
        rag.ingest_text(f"doc{i}", f"hello {i} world", f"/doc/{i}", tags=["t"], meta={"kind": "test"})
        logger.log("concurrent_event", i=i)

    n = 64
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(worker, range(n)))

    idx = json.loads(open(store.index_path, "r", encoding="utf-8").read())
    assert isinstance(idx.get("artifacts"), list)
    assert len(idx["artifacts"]) == n

    rag_data = json.loads(open(os.path.join(run_dir, "rag_index.json"), "r", encoding="utf-8").read())
    assert isinstance(rag_data.get("docs"), dict)
    assert len(rag_data["docs"]) == n

    lines = [ln for ln in open(os.path.join(run_dir, "run.jsonl"), "r", encoding="utf-8").read().splitlines() if ln.strip()]
    assert len(lines) == n
    for ln in lines[:10]:
        obj = json.loads(ln)
        assert obj.get("event") == "concurrent_event"

