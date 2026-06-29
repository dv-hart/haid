"""haid submit: the side-effect-free parts — preview, entry writing, repo detection, and
the git/gh command sequence. The clone-free API path is exercised through an injected
command runner (no real network); only `_default_run` actually shells out to `gh`."""

import base64
import json
from types import SimpleNamespace

import pytest

from haid.report import benchmark, submit

SCORES_DOC = {"window": "w", "episodes": [
    {"id": "ep1", "has_artifact": True, "normalized_tokens": 100.0,
     "difficulty": {"rung": 8.0, "percentile": 0.89},
     "cleanliness": {"severe_count": 1, "minor_count": 0, "other_count": 0,
                     "changed_lines": 100, "by_class": {"dead_code": 1}, "execution_C": 0.77},
     "achievement": 50.0, "value": 0.5,
     "achievement_components": {"volume_loc": 120.0, "volume_term": 11.0,
                                "difficulty_D": 9.0, "cleanliness_C": 0.77}},
]}


def _payload(user="dv-hart"):
    return benchmark.build_submission(SCORES_DOC, github_username=user, project="HAID",
                                      generated_at="T")


def test_preview_shows_public_fields_only():
    text = submit.render_public_preview(_payload())
    assert "PUBLIC and PERMANENT" in text
    assert "dv-hart" in text and "HAID" in text
    # the preview must never carry leak-shaped content
    assert "session" not in text.lower() and "/ep1" not in text


def test_write_entry_roundtrips_and_names_by_user(tmp_path):
    dest = submit.write_entry(_payload("alice"), tmp_path)
    assert dest == tmp_path / "entries" / "alice.json"
    saved = json.loads(dest.read_text(encoding="utf-8"))
    assert saved["github_username"] == "alice" and benchmark.verify(saved)


def test_write_entry_refuses_unverifiable(tmp_path):
    p = _payload()
    p["value_overall"] = 999.0          # break the content hash
    with pytest.raises(ValueError, match="content_hash"):
        submit.write_entry(p, tmp_path)


def test_pr_commands_touch_exactly_the_one_entry():
    cmds = submit.pr_commands("dv-hart", "HAID")
    adds = [c for c in cmds if c[:2] == ["git", "add"]]
    assert adds == [["git", "add", "entries/dv-hart.json"]]
    assert any(c[0] == "gh" and "pr" in c for c in cmds)
    # the PR targets the data-only repo, not the package repo
    assert any(submit.BENCHMARK_REPO in c for c in cmds if c[0] == "gh")


def test_find_repo_root_via_marker(tmp_path):
    (tmp_path / submit.REPO_MARKER).write_text("", encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert submit.find_repo_root(sub) == tmp_path
    # a dir without the marker is not the data repo
    assert submit.find_repo_root(tmp_path.parent) is None


# --- clone-free API path ----------------------------------------------------------------

def test_encode_entry_is_base64_of_the_on_disk_text():
    p = _payload("alice")
    assert base64.b64decode(submit.encode_entry(p)).decode("utf-8") == submit.entry_text(p)


def test_targets_owner_pushes_direct_contributor_forks():
    up = submit.BENCHMARK_REPO            # dv-hart/haid-benchmark
    assert submit._targets("dv-hart", up) == (up, "benchmark/dv-hart", "benchmark/dv-hart")
    assert submit._targets("alice", up) == (
        "alice/haid-benchmark", "benchmark/alice", "alice:benchmark/alice")


def test_api_plan_first_submission_for_contributor():
    plan = submit.api_plan(_payload("alice"), "HAID")
    assert plan[0] == ["gh", "repo", "fork", submit.BENCHMARK_REPO, "--clone=false"]
    pr = plan[-1]
    assert pr[:5] == ["gh", "pr", "create", "--repo", submit.BENCHMARK_REPO]
    assert "alice:benchmark/alice" in pr            # fork PR uses an owner-qualified head
    # exactly one entry file is ever touched
    assert any(f"contents/{submit.entry_relpath('alice')}" in c for cmd in plan for c in cmd)


def test_api_plan_owner_skips_the_fork():
    plan = submit.api_plan(_payload("dv-hart"), "HAID")
    assert not any(c[:3] == ["gh", "repo", "fork"] for c in plan)
    assert "--head" in plan[-1] and "benchmark/dv-hart" in plan[-1]


class _FakeGh:
    """Scripted `gh` runner: records every call and returns canned CompletedProcess-likes
    so submit_via_api can be driven without a network."""

    def __init__(self, *, file_exists=False, ref_exists=False, pr_exists=False):
        self.calls = []
        self.file_exists, self.ref_exists, self.pr_exists = file_exists, ref_exists, pr_exists
        self.PR = "https://github.com/dv-hart/haid-benchmark/pull/7"

    def __call__(self, cmd):
        self.calls.append(cmd)
        s = " ".join(cmd)
        ok = lambda out="": SimpleNamespace(returncode=0, stdout=out, stderr="")
        fail = lambda err="x": SimpleNamespace(returncode=1, stdout="", stderr=err)
        if cmd[:3] == ["gh", "repo", "fork"]:
            return ok()
        if "git/ref/heads/" in s and "git/refs" not in s:          # upstream tip sha
            return ok("SHA123")
        if "-X" in cmd and "PATCH" in s and "git/refs/heads" in s:  # force-update ref
            return ok()
        if "git/refs" in s and "-X" not in cmd:                     # create ref
            return fail("Reference already exists") if self.ref_exists else ok()
        if "contents/" in s and "-X" not in cmd:                    # existing blob sha
            return ok("BLOBSHA") if self.file_exists else fail("Not Found")
        if "-X" in cmd and "PUT" in s and "contents/" in s:         # write the file
            return ok()
        if cmd[:3] == ["gh", "pr", "create"]:
            return fail("a pull request already exists") if self.pr_exists else ok(self.PR)
        if cmd[:3] == ["gh", "pr", "list"]:
            return ok(self.PR) if self.pr_exists else ok("")
        raise AssertionError(f"unexpected gh call: {cmd}")


def _put_cmd(calls):
    return next(c for c in calls if "-X" in c and "PUT" in " ".join(c) and "contents/" in " ".join(c))


def test_submit_via_api_first_submission(monkeypatch):
    gh = _FakeGh()
    url = submit.submit_via_api(_payload("alice"), "HAID", run=gh)
    assert url == gh.PR
    assert ["gh", "repo", "fork", submit.BENCHMARK_REPO, "--clone=false"] in gh.calls
    # creating a brand-new file must NOT pass a blob sha
    assert not any(a.startswith("sha=") for a in _put_cmd(gh.calls))
    # and the uploaded content is exactly our entry bytes
    content = next(a for a in _put_cmd(gh.calls) if a.startswith("content="))[len("content="):]
    assert base64.b64decode(content).decode("utf-8") == submit.entry_text(_payload("alice"))


def test_submit_via_api_update_path_carries_blob_sha_and_force_updates_ref():
    gh = _FakeGh(file_exists=True, ref_exists=True)
    submit.submit_via_api(_payload("alice"), "HAID", run=gh)
    assert any("-X" in c and "PATCH" in " ".join(c) for c in gh.calls)   # ref force-updated
    assert "sha=BLOBSHA" in _put_cmd(gh.calls)                            # update needs the sha


def test_submit_via_api_owner_does_not_fork():
    gh = _FakeGh()
    submit.submit_via_api(_payload("dv-hart"), "HAID", run=gh)
    assert not any(c[:3] == ["gh", "repo", "fork"] for c in gh.calls)
    pr = next(c for c in gh.calls if c[:3] == ["gh", "pr", "create"])
    assert pr[pr.index("--head") + 1] == "benchmark/dv-hart"             # unqualified head


def test_submit_via_api_reuses_open_pr():
    gh = _FakeGh(pr_exists=True)
    assert submit.submit_via_api(_payload("alice"), "HAID", run=gh) == gh.PR
    assert any(c[:3] == ["gh", "pr", "list"] for c in gh.calls)


def test_submit_via_api_refuses_unverifiable():
    p = _payload()
    p["value_overall"] = 999.0
    with pytest.raises(ValueError, match="content_hash"):
        submit.submit_via_api(p, "HAID", run=_FakeGh())
