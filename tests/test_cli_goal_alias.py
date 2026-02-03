import os


def test_cli_supports_goal_without_subcommand(monkeypatch, tmp_path):
    class DummyRunner:
        def __init__(self, cwd, no_websearch=False):
            self.cwd = cwd
            self.no_websearch = no_websearch

        def run(self, goal: str):
            run_dir = tmp_path / "runs" / "run-1"
            (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
            summary = run_dir / "artifacts" / "run_summary.json"
            summary.write_text("{\"contract_reports\": [], \"plan_snapshots\": null}", encoding="utf-8")
            transcript = run_dir / "transcript.md"
            transcript.write_text("# t", encoding="utf-8")
            log = run_dir / "run.jsonl"
            log.write_text("", encoding="utf-8")
            return {
                "artifact_dir": str(run_dir / "artifacts"),
                "log_path": str(log),
                "summary_path": str(summary),
                "transcript_path": str(transcript),
            }

    monkeypatch.setattr("lattice.cli.RouterRunner", DummyRunner)
    monkeypatch.chdir(tmp_path)
    from lattice import cli

    assert cli.main(["--goal", "hello world"]) == 0


def test_cli_run_accepts_goal_flag(monkeypatch, tmp_path):
    class DummyRunner:
        def __init__(self, cwd, no_websearch=False):
            self.cwd = cwd

        def run(self, goal: str):
            run_dir = tmp_path / "runs" / "run-2"
            (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
            summary = run_dir / "artifacts" / "run_summary.json"
            summary.write_text("{\"contract_reports\": [], \"plan_snapshots\": null}", encoding="utf-8")
            transcript = run_dir / "transcript.md"
            transcript.write_text("# t", encoding="utf-8")
            log = run_dir / "run.jsonl"
            log.write_text("", encoding="utf-8")
            return {
                "artifact_dir": str(run_dir / "artifacts"),
                "log_path": str(log),
                "summary_path": str(summary),
                "transcript_path": str(transcript),
            }

    monkeypatch.setattr("lattice.cli.RouterRunner", DummyRunner)
    monkeypatch.chdir(tmp_path)
    from lattice import cli

    assert cli.main(["run", "--goal", "hello world"]) == 0
