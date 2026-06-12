"""Per-episode bridge inputs — `episode_inputs` over an episode's session subset (step 4).

The load-bearing case: a file edited across two episodes must yield each episode's OWN delta
(episode-relative baseline), not the whole change — which falls out of running the bridge over
only that episode's sessions. Run: PYTHONPATH=src python -m pytest tests/bridge/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.bridge import episode_inputs
from haid.session import records as rec
from haid.session.forest import Forest

CWD = "/proj"


class FakeSession:
    def __init__(self, path, records):
        self.path = path
        self.parse = type("P", (), {"records": records})()
        self.subagents = []
        self.forest = Forest(records)

    def warnings(self):
        return []


def _r(d):
    return rec.from_dict(d)


def edit_session(stem, path, original, old, new, usage_in=120):
    """A one-edit session: `old`→`new` on `path`, with `original` as the captured pre-state."""
    recs = [
        _r({"type": "user", "uuid": f"u_{stem}", "parentUuid": None, "timestamp": f"{stem}T10:00:00Z",
            "cwd": CWD, "message": {"role": "user", "content": "edit it"}}),
        _r({"type": "assistant", "uuid": f"a_{stem}", "parentUuid": f"u_{stem}",
            "timestamp": f"{stem}T10:00:01Z", "cwd": CWD,
            "message": {"role": "assistant", "model": "claude-haiku-4-5",
                        "usage": {"input_tokens": usage_in, "output_tokens": 40},
                        "content": [{"type": "tool_use", "id": f"c_{stem}", "name": "Edit",
                                     "input": {"file_path": path, "old_string": old, "new_string": new}}]}}),
        _r({"type": "user", "uuid": f"r_{stem}", "parentUuid": f"a_{stem}", "timestamp": f"{stem}T10:00:02Z",
            "cwd": CWD, "message": {"role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": f"c_{stem}"}]},
            "toolUseResult": {"filePath": path, "originalFile": original,
                              "oldString": old, "newString": new}}),
        _r({"type": "last-prompt", "leafUuid": f"a_{stem}"}),
    ]
    # stems are dates here so window order is well-defined; pad to 8 chars for the id scheme.
    return FakeSession(f"/x/{stem}.jsonl", recs)


def test_episode_diff_is_its_own_delta_not_the_whole_change():
    # foo.py: v0 → v1 in episode 1's session, then v1 → v2 in episode 2's session.
    s1 = edit_session("20260601", "/proj/foo.py", "v0\n", "v0", "v1")
    s2 = edit_session("20260602", "/proj/foo.py", "v1\n", "v1", "v2")

    d1 = episode_inputs([s1]).diff
    d2 = episode_inputs([s2]).diff

    assert "-v0" in d1 and "+v1" in d1 and "v2" not in d1      # episode 1 = v0→v1 only
    assert "-v1" in d2 and "+v2" in d2 and "v0" not in d2      # episode 2 = v1→v2 only


def test_episode_cost_is_summed_over_its_sessions():
    s1 = edit_session("20260601", "/proj/foo.py", "v0\n", "v0", "v1", usage_in=100)
    s2 = edit_session("20260602", "/proj/foo.py", "v1\n", "v1", "v2", usage_in=300)
    c1 = episode_inputs([s1]).cost.normalized_tokens
    c2 = episode_inputs([s2]).cost.normalized_tokens
    both = episode_inputs([s1, s2]).cost.normalized_tokens
    assert c1 > 0 and c2 > c1                                   # more input tokens → more cost
    assert abs(both - (c1 + c2)) < 1e-6                         # episode cost = sum of sessions


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
