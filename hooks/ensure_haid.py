#!/usr/bin/env python3
"""SessionStart hook: make sure the ``haid`` CLI is installed.

The HAID plugin ships only the ``haid-report`` skill (the instructions). The
skill is useless without the ``haid`` Python CLI on PATH, so this hook closes
that gap on the user's behalf.

Discipline:
- **Quiet when healthy.** If ``haid`` already resolves on PATH we emit nothing
  and exit 0, so nothing is injected into the session context.
- **Speak only when we acted.** A one-line note on stdout (exit 0 -> becomes
  context) tells the user/agent that an install happened or that manual action
  is needed. We never block the session — SessionStart can't, and shouldn't.

The hook command tries ``python`` then ``python3`` so it runs on Windows
(cmd.exe, where ``python`` is canonical) and on macOS/Linux (where ``python3``
is). pip installs into the same interpreter that runs this script, which is the
one whose Scripts/bin dir Claude Code's shell should already have on PATH.
"""
import shutil
import subprocess
import sys


def main() -> int:
    if shutil.which("haid"):
        return 0  # already available — stay silent, add no context

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "haid"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pip missing, offline, PEP-668 managed env, etc.
        print(
            "HAID: the `haid` CLI is not installed and auto-install failed "
            f"({exc.__class__.__name__}). Install it manually: pip install haid"
        )
        return 0  # never block the session on a failed convenience install

    if shutil.which("haid"):
        print(
            "HAID: installed the `haid` CLI (pip install haid). "
            "The /haid:haid-report skill is ready — ask \"how am I doing?\"."
        )
    else:
        print(
            "HAID: ran `pip install haid`, but `haid` is still not on PATH. "
            "Add your Python scripts directory to PATH (e.g. ~/.local/bin)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
