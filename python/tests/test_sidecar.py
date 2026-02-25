"""Tests for intermap sidecar mode."""

import json
import os
import subprocess
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "../.."))
INTERMAP_ROOT = PYTHON_DIR


def _start_sidecar():
    """Start the sidecar subprocess and wait for the ready signal."""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "intermap", "--sidecar"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.path.join(PYTHON_DIR, "python")},
        text=True,
    )
    # Read the ready signal
    ready_line = proc.stdout.readline()
    ready = json.loads(ready_line)
    assert ready["status"] == "ready", f"Expected ready signal, got: {ready_line}"
    return proc


def _send_request(proc, req_id, command, project, args=None):
    """Send a JSON-RPC request and read the response."""
    req = {"id": req_id, "command": command, "project": project, "args": args or {}}
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, "Sidecar returned empty response (process may have crashed)"
    return json.loads(line)


def test_sidecar_ready_signal():
    proc = _start_sidecar()
    try:
        # Already verified in _start_sidecar
        pass
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)


def test_sidecar_single_request():
    proc = _start_sidecar()
    try:
        resp = _send_request(proc, 1, "structure", INTERMAP_ROOT,
                             {"language": "python", "max_results": 3})
        assert resp["id"] == 1
        assert "result" in resp
        assert "files" in resp["result"]
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)


def test_sidecar_multiple_requests():
    proc = _start_sidecar()
    try:
        # Send 3 requests in sequence
        for i in range(1, 4):
            resp = _send_request(proc, i, "structure", INTERMAP_ROOT,
                                 {"language": "python", "max_results": 2})
            assert resp["id"] == i
            assert "result" in resp
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)


def test_sidecar_error_handling():
    proc = _start_sidecar()
    try:
        # Unknown command
        resp = _send_request(proc, 1, "nonexistent_command", INTERMAP_ROOT)
        assert resp["id"] == 1
        # dispatch returns {"error": "UnknownCommand", ...} as a result (not an exception)
        assert "result" in resp
        assert resp["result"]["error"] == "UnknownCommand"
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)


def test_sidecar_bad_json():
    proc = _start_sidecar()
    try:
        # Send invalid JSON
        proc.stdin.write("not valid json\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        resp = json.loads(line)
        assert resp["id"] is None
        assert "error" in resp
        assert resp["error"]["type"] == "InvalidJSON"

        # Sidecar should still be alive — send a valid request
        resp = _send_request(proc, 2, "structure", INTERMAP_ROOT,
                             {"language": "python", "max_results": 1})
        assert resp["id"] == 2
        assert "result" in resp
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)


def test_sidecar_clean_exit_on_eof():
    proc = _start_sidecar()
    proc.stdin.close()  # Send EOF
    exit_code = proc.wait(timeout=5)
    assert exit_code == 0, f"Sidecar exited with code {exit_code}"
