"""Assemble viz/data.js for the visualizer prototype.

Bundles a few real extracted sessions + the window-level metrics (for overlays) into a
single `window.HAID_DATA = {...}` JS file. Loading via <script src> (not fetch) means the
wireframes open straight from file:// in Chrome with no server and no CORS friction —
exactly the delivery model proposed for the real `haid report` (a self-contained artifact).

Run scripts/viz_extract.py for each session first, then this.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path("out")
VIZ = Path("viz")
SESSIONS = ["viz_5e6bdb6d.json", "viz_8c2a3afb.json", "viz_bccbf167.json",
            "viz_a6931f78.json", "viz_b3e4bd55.json"]


def session_title(d: dict) -> str:
    """First user prompt of the session — a human label for the session row."""
    for it in d.get("spine", []):
        if it.get("kind") == "user" and it.get("text"):
            return it["text"][:80]
    return d["stem"]


def first_ts(d: dict) -> str:
    for it in d.get("spine", []):
        if it.get("ts"):
            return it["ts"]
    return ""


def group_episodes(sessions: dict) -> list[dict]:
    """Cluster sessions into episodes by shared-file overlap (prototype stand-in for
    `haid episodes`). Ubiquitous files (in >half the sessions — hub files like
    haid-project.md/README) are dropped from the signal so episodes split by the
    topic-specific files actually worked on, not the shared scaffolding."""
    stems = list(sessions)
    fsets = {s: {f["file"] for f in sessions[s].get("files", [])} for s in stems}
    from collections import Counter
    freq = Counter(f for s in stems for f in fsets[s])
    common = {f for f, n in freq.items() if n > len(stems) / 2}
    sig = {s: (fsets[s] - common) for s in stems}

    def jacc(a, b):
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    # connected components where overlap >= threshold
    THRESH = 0.12
    parent = {s: s for s in stems}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i, a in enumerate(stems):
        for b in stems[i + 1:]:
            if jacc(sig[a], sig[b]) >= THRESH:
                parent[find(a)] = find(b)
    groups: dict[str, list] = {}
    for s in stems:
        groups.setdefault(find(s), []).append(s)

    episodes = []
    for i, (_, members) in enumerate(sorted(groups.items(),
                                             key=lambda kv: min(first_ts(sessions[m]) for m in kv[1]))):
        members.sort(key=lambda m: first_ts(sessions[m]))
        # title = the most-shared topic file across the episode, else generic
        shared = Counter(f for m in members for f in sig[m])
        top = shared.most_common(1)
        title = (top[0][0].split("/")[-1] if top else f"episode {i + 1}")
        episodes.append({
            "id": f"ep{i}",
            "title": f"Episode {i + 1} — {title}",
            "session_stems": members,
        })
    return episodes


def main():
    VIZ.mkdir(exist_ok=True)
    sessions = {}
    for fn in SESSIONS:
        p = OUT / fn
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        sessions[d["stem"]] = d

    # window metrics → per-file flag map + headline measurements
    metrics = json.loads((OUT / "haid_metrics.json").read_text(encoding="utf-8"))
    headline = [
        {k: m.get(k) for k in ("metric", "scope", "rate", "token_rate",
                               "token_weight", "baseline")}
        for m in metrics["measurements"] if m["scope"] == "window"
    ]
    # file_id -> set of metrics flagged (any scope), with the heaviest weight
    flags: dict[str, dict] = {}
    for inst in metrics["instances"]:
        fid = inst.get("refs", {}).get("file_id")
        if not fid:
            continue
        rec = flags.setdefault(fid, {"metrics": {}, "weight": 0})
        rec["metrics"][inst["metric"]] = max(
            rec["metrics"].get(inst["metric"], 0), inst["token_weight"])
        rec["weight"] += inst["token_weight"]

    # session titles + episode grouping (window > episodes > sessions hierarchy)
    for s, d in sessions.items():
        d["title"] = session_title(d)
        d["first_ts"] = first_ts(d)
    episodes = group_episodes(sessions)

    bundle = {
        "generated_for": "visualizer prototype — real HAID session data",
        "window_label": metrics["window"]["label"].replace("�", "—"),
        "headline": headline,
        "flags": flags,
        "episodes": episodes,
        "sessions": sessions,
    }
    dest = VIZ / "data.js"
    dest.write_text("window.HAID_DATA = " + json.dumps(bundle, indent=1) + ";\n",
                    encoding="utf-8")
    kb = dest.stat().st_size // 1024
    print(f"wrote {dest} ({kb} KB) — {len(sessions)} sessions, "
          f"{len(episodes)} episodes, {len(flags)} flagged files")
    for ep in episodes:
        print(f"  {ep['title']}: {ep['session_stems']}")


if __name__ == "__main__":
    main()
