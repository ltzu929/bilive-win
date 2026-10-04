"""Run a PowerShell task without allocating a console, and retain its exit code."""
import os
from pathlib import Path
import subprocess
import sys
import traceback


def main():
    script = Path(sys.argv[1]).resolve()
    log_dir = script.parent / "logs" / "startup"
    log_dir.mkdir(parents=True, exist_ok=True)
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    with (log_dir / f"{script.stem}.out.log").open("ab") as stdout, (
        log_dir / f"{script.stem}.err.log"
    ).open("ab") as stderr:
        try:
            return subprocess.run(
                [str(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script)],
                cwd=script.parent,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).returncode
        except Exception:
            stderr.write(traceback.format_exc().encode("utf-8"))
            return 1


if __name__ == "__main__":
    sys.exit(main())
