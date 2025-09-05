import argparse
import os
import sys
import time

from .worker import WorkerRunner


def cmd_run(args: argparse.Namespace) -> int:
    prompt = args.prompt
    runner = WorkerRunner(cwd=os.getcwd())
    try:
        result = runner.run(prompt=prompt, use_rag=(not args.no_rag))
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    print(result["artifact_path"])
    print(result["log_path"])
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
    run_dir = os.path.join(os.getcwd(), "runs", run_id)
    log_path = os.path.join(run_dir, "run.jsonl")
    if not os.path.exists(log_path):
        print(f"No log file found: {log_path}")
        return 1
    if args.follow:
        tail_follow(log_path)
        return 0
    else:
        with open(log_path, "r", encoding="utf-8") as f:
            sys.stdout.write(f.read())
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lattice", description="LATTICE worker runner")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="Run a single worker turn")
    pr.add_argument("prompt", help="Prompt text, wrap in quotes")
    pr.add_argument("--no-rag", action="store_true", help="Disable RAG for this run")
    pr.set_defaults(func=cmd_run)

    pl = sub.add_parser("logs", help="Show logs for a run")
    pl.add_argument("run_id", help="Run ID, e.g., run-20240101-000000-abc123")
    pl.add_argument("--follow", "-f", action="store_true", help="Follow (tail -f) the log file")
    pl.set_defaults(func=cmd_logs)

    return p


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
