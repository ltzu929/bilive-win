"""Run a PowerShell task without allocating a console, and retain its exit code."""
import os
from pathlib import Path
import subprocess
import sys
import traceback
from datetime import datetime, timezone


def main():
    script = Path(sys.argv[1]).resolve()
    log_dir = script.parent / "logs" / "startup"
    log_dir.mkdir(parents=True, exist_ok=True)
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    with (log_dir / f"{script.stem}.out.log").open("ab") as stdout, (
        log_dir / f"{script.stem}.err.log"
    ).open("ab") as stderr:
        try:
            process = subprocess.Popen(
                [str(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script)],
                cwd=script.parent,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stderr.write(
                f"{datetime.now(timezone.utc).isoformat()} task_start script={script.name} pid={process.pid}\n".encode("utf-8")
            )
            stderr.flush()
            result = process.wait()
            stderr.write(
                f"{datetime.now(timezone.utc).isoformat()} task_exit script={script.name} pid={process.pid} code={result}\n".encode("utf-8")
            )
            stderr.flush()
            return result
        except Exception:
            stderr.write(traceback.format_exc().encode("utf-8"))
            return 1


if __name__ == "__main__":
    sys.exit(main())
