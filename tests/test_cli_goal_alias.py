import os


def test_cli_supports_goal_without_subcommand(monkeypatch, tmp_path):
    from lattice.router_main import RouterRunner

    def fake_run(self, goal: str):
        assert isinstance(goal, str) and goal.strip()
        assert os.path.isdir(self.run_dir)
        return {
            "artifact_dir": os.path.join(self.run_dir, "artifacts"),
            "log_path": os.path.join(self.run_dir, "run.jsonl"),
            "summary_path": "",
            "transcript_path": "",
        }

    monkeypatch.setattr(RouterRunner, "run", fake_run, raising=True)
    monkeypatch.setenv("LATTICE_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.chdir(tmp_path)
    from lattice import cli

    assert cli.main(["--goal", "hello world"]) == 0


def test_cli_run_accepts_goal_flag(monkeypatch, tmp_path):
    from lattice.router_main import RouterRunner

    def fake_run(self, goal: str):
        assert isinstance(goal, str) and goal.strip()
        assert os.path.isdir(self.run_dir)
        assert getattr(self, "_web_disabled_by_flag", False) is True
        return {
            "artifact_dir": os.path.join(self.run_dir, "artifacts"),
            "log_path": os.path.join(self.run_dir, "run.jsonl"),
            "summary_path": "",
            "transcript_path": "",
        }

    monkeypatch.setattr(RouterRunner, "run", fake_run, raising=True)
    monkeypatch.setenv("LATTICE_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.chdir(tmp_path)
    from lattice import cli

    assert cli.main(["run", "--goal", "hello world", "--no-websearch", "--provider", "openai"]) == 0
    assert os.environ.get("LATTICE_PROVIDER") == "openai"
