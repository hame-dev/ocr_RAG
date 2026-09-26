"""Sandboxed Python runner for General-mode chat.

The backend POSTs model-written code here; it runs in a throwaway directory
under hard resource limits and comes back as stdout/stderr plus any files it
wrote (charts, spreadsheets, Word/PowerPoint files).

Isolation is layered, and this file is only the innermost layer:
  - the container sits on an internal-only Docker network (no internet), runs
    as a non-root user with a read-only root filesystem, no capabilities, and
    memory / CPU / pid limits (docker-compose.yml);
  - each job is a fresh `python -I` process in its own session, with rlimits
    on memory, CPU time, file size, open files and processes (set by
    sandbox_exec.py), killed outright at the wall-clock timeout;
  - when a job ends, everything it started dies with it, including processes
    that detached into a session of their own.

Standard library only, so the image carries nothing but the libraries the
generated code is meant to use.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8090"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))
DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 120
MAX_CODE_CHARS = 100_000
MAX_OUTPUT_CHARS = 8_000
MEMORY_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 10
MAX_TOTAL_FILE_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".svg", ".pdf",
    ".xlsx", ".docx", ".pptx",
    ".csv", ".txt", ".json", ".md",
}
SCRIPT_NAME = "main.py"
# matplotlib's font cache, built into the image. Copied into each job (a small
# JSON file): matplotlib needs its config dir writable, and without the cache it
# rescans every font on each run.
MPL_CACHE_SEED = os.environ.get("MPL_CACHE_SEED", "")

EXEC_WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_exec.py")

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
# Sessions of the jobs running now. Anything else of ours is a leftover.
_jobs_lock = threading.Lock()
_active_sessions: set[int] = set()


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n[… output truncated: {len(text) - MAX_OUTPUT_CHARS} more characters]"


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _sweep_leftovers() -> None:
    """Kill our processes that belong to no running job.

    Catches what killing a job's group cannot: a background process that
    called setsid() to detach and outlived its job. Left alone, those pile up
    until the container's pid limit stops every later job from starting.
    """
    if not os.path.isdir("/proc"):
        return  # not Linux (a developer machine): nothing to sweep
    uid = os.getuid()
    spared = {os.getpid(), os.getppid(), 1}  # ourselves, and the container's init
    with _jobs_lock:
        keep = {os.getsid(0), *_active_sessions}
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) in spared:
                continue
            try:
                if os.stat(f"/proc/{entry}").st_uid != uid:
                    continue
                with open(f"/proc/{entry}/stat") as fh:
                    stat = fh.read()
                # "pid (comm) state ppid pgrp session ..."; comm may contain spaces.
                session = int(stat.rsplit(")", 1)[1].split()[3])
            except (OSError, ValueError, IndexError):
                continue
            if session not in keep:
                try:
                    os.kill(int(entry), signal.SIGKILL)
                except OSError:
                    pass


def _collect_files(workdir: str) -> tuple[list[dict], list[str]]:
    files: list[dict] = []
    skipped: list[str] = []
    total = 0
    for root, _dirs, names in os.walk(workdir):
        for name in sorted(names):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, workdir)
            if rel == SCRIPT_NAME or os.path.islink(path) or not os.path.isfile(path):
                continue
            # Library caches (matplotlib's font cache) live in the job dir too.
            if rel.split(os.sep)[0].startswith("."):
                continue
            if os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
                skipped.append(f"{rel} (file type not allowed)")
                continue
            size = os.path.getsize(path)
            if len(files) >= MAX_FILES or total + size > MAX_TOTAL_FILE_BYTES:
                skipped.append(f"{rel} (file limit reached)")
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            total += size
            files.append({"name": name, "size": size, "b64": base64.b64encode(data).decode()})
    return files, skipped


def run_job(code: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> dict:
    timeout_s = max(1, min(int(timeout_s), MAX_TIMEOUT_S))
    workdir = tempfile.mkdtemp(prefix="job-")
    started = time.monotonic()
    try:
        with open(os.path.join(workdir, SCRIPT_NAME), "w", encoding="utf-8") as fh:
            fh.write(code)
        mpl_dir = os.path.join(workdir, ".matplotlib")
        if MPL_CACHE_SEED and os.path.isdir(MPL_CACHE_SEED):
            shutil.copytree(MPL_CACHE_SEED, mpl_dir)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": workdir,
            "TMPDIR": workdir,
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": mpl_dir,
            "PYTHONIOENCODING": "utf-8",
            "LANG": "C.UTF-8",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        }
        # Registered under the lock, so a concurrent sweep never sees this job
        # before it counts as running.
        with _jobs_lock:
            process = subprocess.Popen(
                [sys.executable, "-I", EXEC_WRAPPER,
                 str(timeout_s), str(MEMORY_BYTES), str(MAX_FILE_BYTES), SCRIPT_NAME],
                cwd=workdir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,  # session id == process group id == pid
            )
            _active_sessions.add(process.pid)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(process.pid)
            stdout, stderr = process.communicate()
        finally:
            # Also after a clean exit: background children must not outlive the job.
            _kill_group(process.pid)
            with _jobs_lock:
                _active_sessions.discard(process.pid)
            _sweep_leftovers()

        stderr_text = stderr.decode("utf-8", "replace")
        exit_code = process.returncode
        if timed_out:
            stderr_text += f"\nTimeoutError: the code ran longer than {timeout_s} seconds and was stopped."
        elif exit_code == -signal.SIGXCPU:
            stderr_text += "\nTimeoutError: the code used more CPU time than allowed and was stopped."
        elif exit_code == -signal.SIGKILL:
            stderr_text += "\nMemoryError: the code was stopped (it probably used too much memory)."

        files, skipped = _collect_files(workdir)
        if skipped:
            stderr_text += "\nFiles not returned: " + ", ".join(skipped)
        return {
            "ok": exit_code == 0 and not timed_out,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout": _truncate(stdout.decode("utf-8", "replace")),
            "stderr": _truncate(stderr_text.strip()),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "files": files,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "code-runner"

    def _reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._reply(200, {"status": "ok"})
        return self._reply(404, {"detail": "not found"})

    def do_POST(self):
        if self.path != "/run":
            return self._reply(404, {"detail": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                # read(-1) would block until the client closes the connection.
                return self._reply(400, {"detail": "invalid Content-Length"})
            if length > MAX_CODE_CHARS * 4:
                return self._reply(413, {"detail": "request too large"})
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._reply(400, {"detail": "invalid JSON body"})

        code = body.get("code")
        if not isinstance(code, str) or not code.strip():
            return self._reply(400, {"detail": "code is required"})
        if len(code) > MAX_CODE_CHARS:
            return self._reply(413, {"detail": "code is too long"})

        if not _slots.acquire(blocking=False):
            return self._reply(429, {"detail": "the code runner is busy"})
        try:
            result = run_job(code, body.get("timeout_s") or DEFAULT_TIMEOUT_S)
        finally:
            _slots.release()
        return self._reply(200, result)

    def log_message(self, fmt, *args):  # one line per request, no bodies
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"code-runner listening on :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
