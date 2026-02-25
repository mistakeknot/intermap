"""CLI entry point: python3 -m intermap

Modes:
  --command/--project/--args  Single-shot analysis (original mode)
  --sidecar                   Persistent stdin/stdout JSON-RPC loop
"""

import argparse
import json
import sys
import traceback


def main():
    parser = argparse.ArgumentParser(description="Intermap analysis CLI")
    parser.add_argument("--sidecar", action="store_true",
                        help="Run as persistent sidecar (stdin/stdout JSON-RPC)")
    parser.add_argument("--command", help="Analysis command to run")
    parser.add_argument("--project", help="Project path")
    parser.add_argument("--args", default="{}", help="JSON-encoded arguments")
    args = parser.parse_args()

    if args.sidecar:
        _run_sidecar()
    else:
        if not args.command or not args.project:
            parser.error("--command and --project are required (or use --sidecar)")
        _run_single(args)


def _run_single(args):
    """Original single-shot mode."""
    try:
        extra_args = json.loads(args.args)
    except json.JSONDecodeError as e:
        _error_exit("InvalidArgs", f"Failed to parse --args JSON: {e}")

    try:
        from .analyze import dispatch
        result = dispatch(args.command, args.project, extra_args)
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
    except FileNotFoundError as e:
        _error_exit("FileNotFoundError", str(e))
    except ImportError as e:
        _error_exit("ImportError", str(e))
    except Exception as e:
        _error_exit(type(e).__name__, str(e))


def _run_sidecar():
    """Persistent sidecar: read JSON requests from stdin, write responses to stdout."""
    from .analyze import dispatch

    # Signal readiness
    sys.stdout.write('{"status":"ready"}\n')
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            resp = {"id": None, "error": {"type": "InvalidJSON", "message": str(e)}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        req_id = req.get("id")
        command = req.get("command", "")
        project = req.get("project", "")
        extra_args = req.get("args", {})

        try:
            result = dispatch(command, project, extra_args)
            resp = {"id": req_id, "result": result}
        except Exception as e:
            resp = {"id": req_id, "error": {"type": type(e).__name__, "message": str(e)}}

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


def _error_exit(error_type: str, message: str):
    """Write structured error to stderr and exit."""
    error = {
        "error": error_type,
        "message": message,
        "traceback": traceback.format_exc(),
    }
    json.dump(error, sys.stderr)
    sys.stderr.write("\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
