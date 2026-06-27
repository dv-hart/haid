"""`haid why` must force an explicit bug-attribution decision — it can never silently fall
back to waste-only (the silent-absence failure this feature was built to end)."""

import json

from haid.cli import build_parser


def _args(argv):
    return build_parser().parse_args(argv)


def test_why_without_tags_or_optout_errors(tmp_path, capsys):
    """Neither --tags nor --no-bug-attribution -> exit 2 with a clear instruction, no run."""
    sess = tmp_path / "aaaaaaaa.jsonl"          # minimal one-record session so a window exists
    sess.write_text(json.dumps({"type": "user", "uuid": "u1", "parentUuid": None,
                                "timestamp": "1", "cwd": "/p",
                                "message": {"role": "user", "content": "hi"}}) + "\n",
                    encoding="utf-8")
    args = _args(["why", "--session", str(sess), "--backend", "replay"])
    rc = args.func(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "bug-attribution decision" in err and "--no-bug-attribution" in err


def test_why_optout_is_explicit_and_proceeds(tmp_path, capsys):
    """--no-bug-attribution lets it run waste-only (here it reaches the replay-needs-notes gate,
    proving it got PAST the attribution gate rather than erroring on it)."""
    sess = tmp_path / "aaaaaaaa.jsonl"
    sess.write_text(json.dumps({"type": "user", "uuid": "u1", "parentUuid": None,
                                "timestamp": "1", "cwd": "/p",
                                "message": {"role": "user", "content": "hi"}}) + "\n",
                    encoding="utf-8")
    args = _args(["why", "--session", str(sess), "--no-bug-attribution",
                  "--backend", "replay"])
    rc = args.func(args)
    err = capsys.readouterr().err
    # past the attribution gate: either "no anchors" (rc 0) or the replay --notes gate (rc 2),
    # but NOT the bug-attribution-decision error.
    assert "bug-attribution decision" not in err
    assert rc in (0, 2)
