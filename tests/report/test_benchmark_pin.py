"""The benchmark-comparability pin guard (scripts/check_benchmark_pin.py), offline half.

This is the loud contributor-facing tripwire: change an anchor ladder or a combiner knob
without refreshing scripts/benchmark_pin.json and this fails with the runbook. The network
half (the benchmark repo actually pins the locked version) runs in CI only — see
.github/workflows/benchmark-pin.yml — so the unit suite stays offline and deterministic.
"""

import hashlib
import importlib.util
from importlib import resources
from pathlib import Path

from haid.report import benchmark

_CHECKER = Path(__file__).resolve().parents[2] / "scripts" / "check_benchmark_pin.py"
_spec = importlib.util.spec_from_file_location("check_benchmark_pin", _CHECKER)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_ladder_versions_are_newline_portable():
    """The comparability hash must NOT depend on checkout line endings — a CRLF working tree
    (Windows) has to hash the same as the LF artifact the gate installs, or that submitter is
    falsely rejected as 'stale'. Regression: ladder_versions once hashed raw file bytes."""
    # The hash equals the LF-normalized hash whether the on-disk file is LF or CRLF: if it's
    # LF, raw==lf; if CRLF, the function strips the CRs to the same bytes. So a CRLF checkout
    # can't produce a different version than the LF artifact.
    for axis, h in benchmark.ladder_versions().items():
        raw = resources.files("haid.data").joinpath(f"{axis}_anchors.json").read_bytes()
        lf = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        assert h == hashlib.sha256(lf).hexdigest()[:16], f"{axis} hash is line-ending-sensitive"


def test_lockfile_matches_this_checkout():
    """If this fails, an anchor ladder or the combiner config changed without a lockfile
    refresh. Run `python scripts/check_benchmark_pin.py --update` and bump the benchmark
    repo's pin (see the runbook the script prints)."""
    problems = guard.check_local(guard.load_lock())
    assert not problems, "\n".join(problems)


def test_lockfile_pins_a_concrete_version():
    """The lockfile must name a benchmark repo and a concrete version for the remote check."""
    lock = guard.load_lock()
    assert lock.get("benchmark_repo")
    assert lock.get("pinned_haid_version")
