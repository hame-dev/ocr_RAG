"""Apply a job's resource limits, then exec the job's script.

    python -I sandbox_exec.py <timeout_s> <memory_bytes> <file_bytes> <script>

server.py starts every job through this instead of a Popen preexec_fn: the
server is threaded, and running Python in the forked child of a threaded
process can deadlock before exec. Here the limits are set in a fresh,
single-threaded interpreter, which then replaces itself with the job.
"""
import os
import resource
import sys


def main() -> None:
    timeout_s, memory_bytes, file_bytes = (int(value) for value in sys.argv[1:4])
    script = sys.argv[4]
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (timeout_s, timeout_s + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        # Per-user, so it only bites where the runner has its own uid (the container).
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    except (ValueError, OSError):
        pass
    os.execv(sys.executable, [sys.executable, "-I", "-X", "utf8", script])


if __name__ == "__main__":
    main()
