import argparse
import os
import sys
import time
import json
from json import JSONDecodeError

from .router import RouterRunner
from .secrets import redact_secrets
from .constants import get_runs_base_dir
from .config import ConfigurationFactory


def cmd_run(args: argparse.Namespace) -> int:
    prompt = getattr(args, "prompt", None) or getattr(args, "goal", None)
    if not isinstance(prompt, str) or not prompt.strip():
        print("ERROR: missing prompt/goal. Use `lattice run \"...\"` or `lattice --goal \"...\"`.")
        return 2
    if getattr(args, "provider", None):
        os.environ["LATTICE_PROVIDER"] = args.provider
    if getattr(args, "router_provider", None):
        os.environ["LATTICE_ROUTER_PROVIDER"] = args.router_provider
    if getattr(args, "router_model", None):
        os.environ["LATTICE_ROUTER_MODEL"] = args.router_model
    if getattr(args, "model", None):
        os.environ["OPENAI_MODEL"] = args.model
        os.environ["LATTICE_ROUTER_MODEL"] = args.model
        os.environ["LATTICE_AGENT_MODEL"] = args.model
    if getattr(args, "no_rag", False):
        os.environ["LATTICE_USE_RAG"] = "0"
    if getattr(args, "huddles", None):
        os.environ["LATTICE_HUDDLES"] = args.huddles
    runner = RouterRunner(cwd=os.getcwd(), no_websearch=getattr(args, "no_websearch", False))
    run_id = getattr(runner, "run_id", None)
    run_dir = getattr(runner, "run_dir", None)
    if isinstance(run_id, str) and run_id and isinstance(run_dir, str) and run_dir:
        print(f"Starting run: {run_id}")
        print(f"- run dir:   {run_dir}")
        print(f"- logs:      {os.path.join(run_dir, 'run.jsonl')}")
        print(f"- workspace: {os.path.join(run_dir, 'workspace')}")
        print(f"- follow:    lattice logs {run_id} --follow")
        sys.stdout.flush()
    try:
        result = runner.run(goal=prompt)
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    summary_path = result.get("summary_path")
    transcript_path = result.get("transcript_path")
    print("Run complete.")
    print(f"- artifacts: {result.get('artifact_dir')}")
    print(f"- logs:      {result.get('log_path')}")
    if result.get("workspace_dir"):
        print(f"- workspace: {result.get('workspace_dir')}")
    if transcript_path and os.path.exists(transcript_path):
        print(f"- transcript:{transcript_path}")
    if summary_path and os.path.exists(summary_path):
        print(f"- summary:   {summary_path}")
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                s = json.load(f)
            print("- contracts: ", ", ".join(s.get("contract_reports", [])[:3]))
            print("- snapshots: ", s.get("plan_snapshots"))
            run_dir = os.path.dirname(os.path.dirname(summary_path))
            log_path = os.path.join(run_dir, "run.jsonl")
            router_line = None
            agent_counts = {}
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8") as lf:
                    for line in lf:
                        try:
                            obj = json.loads(line)
                        except JSONDecodeError:
                            continue
                        if obj.get("event") == "router_llm_turn":
                            router_line = obj
                        if obj.get("event") == "agent_model_turn":
                            prov = obj.get("provider")
                            agent_counts[prov] = agent_counts.get(prov, 0) + 1
            if router_line:
                print(f"- provider mix: Router: {router_line.get('provider')}/{router_line.get('model')}")
            if agent_counts:
                total = sum(agent_counts.values())
                lm = agent_counts.get("lmstudio", 0)
                primary = max(((p, c) for p, c in agent_counts.items() if p != "lmstudio"), key=lambda x: x[1], default=("?", 0))[0]
                print(f"                Agents: {primary} (+ {lm} lmstudio fallbacks)")
        except (OSError, JSONDecodeError, ValueError, TypeError):
            pass
    return 0


def tail_follow(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.25)
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
    except KeyboardInterrupt:
        return


def cmd_logs(args: argparse.Namespace) -> int:
    run_id = args.run_id
    run_dir = os.path.join(get_runs_base_dir(), run_id)
    log_path = os.path.join(run_dir, "run.jsonl")
    if not os.path.exists(log_path):
        print(f"No log file found: {log_path}")
        return 1
    if args.output_only:
        try:
            def handle_obj(obj: dict):
                if obj.get("event") == "model_call":
                    out = obj.get("output")
                    if out is None:
                        return
                    if isinstance(out, str) and out.strip() == "":
                        return
                    ts = obj.get("ts", "")
                    prov = obj.get("provider", "")
                    model = obj.get("model", "")
                    header = f"[{ts}] {prov} {model}".strip()
                    print(header)
                    print("-" * len(header))
                    print(str(out))
                    print()

            if args.follow:
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                        except JSONDecodeError:
                            continue
                        handle_obj(obj)
                    while True:
                        line = f.readline()
                        if not line:
                            time.sleep(0.25)
                            continue
                        try:
                            obj = json.loads(line)
                        except JSONDecodeError:
                            continue
                        handle_obj(obj)
            else:
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                        except JSONDecodeError:
                            continue
                        handle_obj(obj)
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            print(f"ERROR reading log: {e}")
            return 1
    else:
        if args.follow:
            tail_follow(log_path)
            return 0
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    sys.stdout.write(line)
            sys.stdout.flush()
            return 0
        except OSError as e:
            print(f"ERROR reading log: {e}")
            return 1


def _scrub_run_dir(run_dir: str) -> int:
    changed = 0
    cfg_path = os.path.join(run_dir, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            obj = redact_secrets(obj)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(obj, f, indent=2)
            changed += 1
        except (OSError, JSONDecodeError, ValueError, TypeError):
            pass
    log_path = os.path.join(run_dir, "run.jsonl")
    if os.path.exists(log_path):
        try:
            new_lines = []
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                        obj = redact_secrets(obj)
                        new_lines.append(json.dumps(obj, ensure_ascii=False))
                    except JSONDecodeError:
                        new_lines.append(line.rstrip("\n"))
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")
            changed += 1
        except OSError:
            pass
    return changed


def cmd_scrub(args: argparse.Namespace) -> int:
    base = get_runs_base_dir()
    if not os.path.isdir(base):
        print("No runs directory found")
        return 0
    if args.run_id:
        target = os.path.join(base, args.run_id)
        if not os.path.isdir(target):
            print(f"Run dir not found: {target}")
            return 1
        changed = _scrub_run_dir(target)
        print(f"Scrubbed {args.run_id}: {changed} files redacted")
        return 0
    total = 0
    for name in os.listdir(base):
        run_dir = os.path.join(base, name)
        if os.path.isdir(run_dir):
            total += _scrub_run_dir(run_dir)
    print(f"Scrubbed all runs: {total} files redacted")
    return 0


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def cmd_config_show(args: argparse.Namespace) -> int:
    cfg = ConfigurationFactory.load_user_config()
    path = ConfigurationFactory._user_config_path()
    print(f"Config path: {path}")
    print(json.dumps(cfg or {}, indent=2))
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    cfg = ConfigurationFactory.load_user_config()
    if not isinstance(cfg, dict):
        cfg = {}
    policy = cfg.get("command_policy", {}) if isinstance(cfg.get("command_policy", {}), dict) else {}
    if args.allowlist is not None:
        policy["allowlist"] = _parse_csv(args.allowlist)
    if args.denylist is not None:
        policy["denylist"] = _parse_csv(args.denylist)
    cfg["command_policy"] = policy
    path = ConfigurationFactory.save_user_config(cfg)
    print(f"Saved config: {path}")
    print(json.dumps(cfg, indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("ERROR: uvicorn is required to run the server. Install with 'pip install uvicorn'.")
        return 1
    if getattr(args, "run_root", None):
        os.environ["LATTICE_RUN_ROOT"] = args.run_root
    host = getattr(args, "host", "127.0.0.1")
    port = int(getattr(args, "port", 5050))
    print(f"Starting LATTICE API at http://{host}:{port}")
    uvicorn.run("lattice.server:app", host=host, port=port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lattice", description="LATTICE multi-agent router")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="Run a single worker turn")
    pr.add_argument("prompt", nargs="?", help="Prompt text, wrap in quotes")
    pr.add_argument("--goal", dest="goal", help="Alias for prompt text (e.g., lattice --goal \"...\")")
    pr.add_argument("--no-rag", action="store_true", help="Disable RAG for this run")
    pr.add_argument("--no-websearch", action="store_true", help="Force web_search to return tool_unavailable (local adapter disabled)")
    pr.add_argument("--provider", dest="provider", help="Provider for both router and agents (e.g., openai, groq, lmstudio)", nargs='?')
    pr.add_argument("--router-provider", dest="router_provider", help="Router provider (e.g., groq or lmstudio)", nargs='?')
    pr.add_argument("--router-model", dest="router_model", help="Router model id (e.g., openai/gpt-oss-120b)", nargs='?')
    pr.add_argument("--model", "-m", dest="model", help="Model to use for both router and agents (e.g., gpt-4o-mini, gpt-4o)")
    pr.add_argument("--huddles", dest="huddles", choices=["dialog", "synthesis"], help="Huddle mode: dialog or synthesis", nargs='?')
    pr.set_defaults(func=cmd_run)

    pl = sub.add_parser("logs", help="Show logs for a run")
    pl.add_argument("run_id", help="Run ID, e.g., run-20240101-000000-abc123")
    pl.add_argument("--follow", "-f", action="store_true", help="Follow (tail -f) the log file")
    pl.add_argument("--output-only", "-O", action="store_true", help="Show only model outputs, nicely formatted")
    pl.set_defaults(func=cmd_logs)

    ps = sub.add_parser("scrub", help="Redact secrets from existing run logs/configs")
    ps.add_argument("run_id", nargs="?", help="Specific run ID to scrub (default: all)")
    ps.set_defaults(func=cmd_scrub)

    pcfg = sub.add_parser("config", help="Show or update persistent config")
    pcfg_sub = pcfg.add_subparsers(dest="action", required=True)
    pcfg_show = pcfg_sub.add_parser("show", help="Show current user config")
    pcfg_show.set_defaults(func=cmd_config_show)
    pcfg_set = pcfg_sub.add_parser("set", help="Set user config values")
    pcfg_set.add_argument("--allowlist", dest="allowlist", help="Comma-separated command allowlist")
    pcfg_set.add_argument("--denylist", dest="denylist", help="Comma-separated command denylist")
    pcfg_set.set_defaults(func=cmd_config_set)

    pserve = sub.add_parser("serve", help="Launch local API server")
    pserve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    pserve.add_argument("--port", type=int, default=5050, help="Bind port (default: 5050)")
    pserve.add_argument(
        "--run-root",
        dest="run_root",
        default=None,
        help="Runs root directory (default: ~/.lattice/runs)",
    )
    pserve.set_defaults(func=cmd_serve)

    return p


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    known_cmds = {"run", "logs", "scrub", "config", "serve"}
    if argv and (argv[0] not in known_cmds) and ("--goal" in argv):
        idx = argv.index("--goal")
        if idx + 1 < len(argv):
            goal = argv[idx + 1]
            rest = argv[:idx] + argv[idx + 2 :]
            argv = ["run", goal] + rest
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
