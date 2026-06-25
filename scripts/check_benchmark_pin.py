"""Guard the cross-repo benchmark-comparability pin so it can't rot silently.

The community board (ADR-0005) only compares rows scored on the SAME anchor ladders and the
SAME combiner config — `validate_entry` rejects anything else as "stale". The benchmark
repo's CI gate runs a PINNED `haid==X.Y.Z` (see its validate.yml + build.yml), so that pin
is what defines "current". The silent failure mode: someone edits an anchor ladder or a
combiner knob, ships a new release, but forgets to bump the benchmark repo's pin — and now
every legitimate latest-version submission is rejected, blaming the submitter.

This guard makes that impossible to do quietly. scripts/benchmark_pin.json records the
comparability hashes plus the version the benchmark repo is expected to pin. Two checks:

  LOCAL  (offline, also run by tests/report/test_benchmark_pin.py): the live hashes computed
         from THIS checkout match the lockfile. Editing an anchor/combiner without running
         `--update` fails here, loudly, with the runbook below.
  REMOTE (network, run by .github/workflows/benchmark-pin.yml): the `haid==` version actually
         pinned in the benchmark repo's two workflows equals the lockfile's pinned version.
         Forgetting the cross-repo bump fails here.

The workflow adds a third, deepest check in YAML: install that pinned release in a clean
venv and confirm ITS hashes match the lockfile — proving the version the gate runs is truly
hash-compatible with what `haid submit` produces today.

  python scripts/check_benchmark_pin.py            # local + remote
  python scripts/check_benchmark_pin.py --offline  # local only (no network)
  python scripts/check_benchmark_pin.py --update    # rewrite the lockfile from this checkout
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

from haid import __version__
from haid.report import benchmark

LOCK = Path(__file__).resolve().parent / "benchmark_pin.json"
# the two benchmark-repo workflows whose `pip install haid==X` defines the gate's "current"
_PINNED_WORKFLOWS = ("validate.yml", "build.yml")
_RAW = "https://raw.githubusercontent.com/{repo}/main/.github/workflows/{wf}"
_PIN_RE = re.compile(r"""haid==([0-9][^"'\s]*)""")

_RUNBOOK = (
    "\nRunbook: if you intentionally changed an anchor ladder or a combiner knob,\n"
    "  1. run `python scripts/check_benchmark_pin.py --update` to refresh the lockfile,\n"
    "  2. cut a new haid release that ships the change,\n"
    "  3. bump `pinned_haid_version` in scripts/benchmark_pin.json AND the `haid==` pins in\n"
    "     dv-hart/haid-benchmark's validate.yml + build.yml to that release.\n"
    "Otherwise legitimate submissions will be rejected as 'stale' (ADR-0005).")


def live_hashes() -> dict:
    """The comparability hashes computed from THIS checkout's shipped data + combiner."""
    return {"ladder_versions": benchmark.ladder_versions(),
            "combiner_config_hash": benchmark.combiner_config_hash()}


def load_lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def check_local(lock: dict) -> list[str]:
    """Lockfile hashes == hashes computed from this checkout. Offline."""
    live = live_hashes()
    problems = []
    if lock.get("ladder_versions") != live["ladder_versions"]:
        problems.append(f"ladder_versions drift: lockfile {lock.get('ladder_versions')} "
                        f"!= live {live['ladder_versions']}")
    if lock.get("combiner_config_hash") != live["combiner_config_hash"]:
        problems.append(f"combiner_config_hash drift: lockfile "
                        f"{lock.get('combiner_config_hash')!r} != live "
                        f"{live['combiner_config_hash']!r}")
    return problems


def _fetch_pin(repo: str, wf: str, *, timeout: float = 20.0) -> str | None:
    url = _RAW.format(repo=repo, wf=wf)
    with urllib.request.urlopen(url, timeout=timeout) as r:        # noqa: S310 (https)
        text = r.read().decode("utf-8")
    m = _PIN_RE.search(text)
    return m.group(1) if m else None


def check_remote(lock: dict) -> list[str]:
    """The benchmark repo's two workflows pin exactly the lockfile's version. Network."""
    repo, want = lock["benchmark_repo"], lock["pinned_haid_version"]
    problems = []
    for wf in _PINNED_WORKFLOWS:
        try:
            got = _fetch_pin(repo, wf)
        except OSError as e:
            problems.append(f"could not fetch {repo}/{wf}: {e}")
            continue
        if got is None:
            problems.append(f"{repo}/{wf}: no `haid==` pin found (gate must pin a version)")
        elif got != want:
            problems.append(f"{repo}/{wf} pins haid=={got}, lockfile expects {want}")
    return problems


def update(lock: dict) -> dict:
    lock.update(live_hashes())
    LOCK.write_text(json.dumps(lock, indent=1) + "\n", encoding="utf-8")
    return lock


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip the cross-repo network check")
    ap.add_argument("--update", action="store_true",
                    help="rewrite the lockfile hashes from this checkout, then exit")
    args = ap.parse_args(argv)

    lock = load_lock()
    if args.update:
        update(lock)
        print(f"updated {LOCK.name} from haid {__version__}: {live_hashes()}")
        return 0

    problems = check_local(lock)
    if not args.offline:
        problems += check_remote(lock)

    if problems:
        print("benchmark pin guard FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(_RUNBOOK, file=sys.stderr)
        return 1
    scope = "local" if args.offline else "local + remote"
    print(f"benchmark pin guard OK ({scope}): haid {__version__}, "
          f"benchmark repo pins {lock['pinned_haid_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
