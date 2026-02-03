import os
import time
import zipfile


def test_deliverables_zip_excludes_seeded_workspace_files(tmp_path):
    from lattice.finalize import _create_deliverables_zip

    run_dir = tmp_path / "run"
    ws = tmp_path / "ws"
    run_dir.mkdir()
    ws.mkdir()

    backend = ws / "backend"
    backend.mkdir()
    public = ws / "public"
    public.mkdir()

    now = time.time()
    baseline = now - 5

    seeded = backend / "seeded.txt"
    seeded.write_text("seeded", encoding="utf-8")
    os.utime(seeded, (baseline - 100, baseline - 100))

    fresh = backend / "fresh.txt"
    fresh.write_text("fresh", encoding="utf-8")
    os.utime(fresh, (baseline + 100, baseline + 100))

    pub_fresh = public / "index.html"
    pub_fresh.write_text("<!doctype html>", encoding="utf-8")
    os.utime(pub_fresh, (baseline + 100, baseline + 100))

    rel = _create_deliverables_zip(str(run_dir), workspace_root=str(ws), run_started_at=baseline)
    zip_path = run_dir / rel
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as zf:
        names = [n.replace("\\", "/") for n in zf.namelist()]

    assert "backend/fresh.txt" in names
    assert "backend/seeded.txt" not in names
    assert "public/index.html" in names
