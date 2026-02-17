"""CLI entry point: python3 -m intermap.analyze"""

import argparse
import json
import sys
import traceback


def main():
    parser = argparse.ArgumentParser(description="Intermap analysis CLI")
    parser.add_argument("--command", required=True, help="Analysis command to run")
    parser.add_argument("--project", required=True, help="Project path")
    parser.add_argument("--args", default="{}", help="JSON-encoded arguments")
    args = parser.parse_args()

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
