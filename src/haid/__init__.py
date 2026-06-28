"""HAID — "How Am I Doing": local-only self-audit & coaching for Claude Code sessions.

This package is the product code (stdlib only). The scoring subpackage places a session
diff against fixed reference ladders to produce relative achievement scores; the model
judgment those placements need is delegated to the host agent (Claude Code subagents),
never an in-process API call — see haid.scoring.compare.
"""

__version__ = "0.0.12"
