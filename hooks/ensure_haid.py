#!/usr/bin/env python3
"""SessionStart hook: check that the ``haid`` CLI is on PATH and, if not, tell
the user how to install it.

The HAID plugin ships only the ``haid-report`` skill (the instructions). The
skill is useless without the ``haid`` Python CLI on PATH, so this hook detects
the gap and surfaces a one-line install hint — it does **not** install anything
itself. Silently running ``pip install`` into the user's interpreter on every
session start is surprising and breaks in PEP-668 / externally-managed
environments; suggesting the command leaves the choice (and the right
environment) to the user.

Discipline:
- **Quiet when healthy.** If ``haid`` already resolves on PATH we emit nothing
  and exit 0, so nothing is injected into the session context.
- **Suggest, never act.** When the CLI is missing we print a single hint line
  (exit 0 -> becomes context) and stop. We never install, and never block the
  session — SessionStart can't, and shouldn't.

The hook command tries ``python`` then ``python3`` so it runs on Windows
(cmd.exe, where ``python`` is canonical) and on macOS/Linux (where ``python3``
is).
"""
import shutil
import sys


def main() -> int:
    if shutil.which("haid"):
        return 0  # already available — stay silent, add no context

    print(
        "HAID: the `haid` CLI isn't on PATH, so the haid-report skill can't run "
        "yet. Install it with `pip install haid` (use the same Python/venv as "
        "this session, e.g. `python -m pip install haid`), then start a new "
        "session and ask \"how am I doing?\"."
    )
    return 0  # informational only — never block the session


if __name__ == "__main__":
    sys.exit(main())
