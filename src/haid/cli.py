"""haid command-line entry.

  haid metrics [--project PATH | --session FILE...] [--days N] [--json] [--top-n N]
      The Phase-1 waste-metrics SUBSTRATE over an analysis window: four metrics at session
      and window scope, each placed against a per-scope baseline. Markdown (eyeball/DoD view)
      by default; --json emits the Phase-2/3 hand-off (docs/metrics-output-schema.md). Pure
      measurement — no remedy/interpretation.

  haid volume --diff PATH
      Deterministic weighted surviving-LOC of a diff (no model).

  haid cost --usage PATH
      Normalized-token cost of a session (weighted by token type + model tier; no $).

  haid place --diff PATH --axis {difficulty,cleanliness} [options]
      Relative placement of a diff on a reference ladder.
      --backend harness (default): emit a comparison job manifest for the host agent to
        run as subagents (the live path); reads verdicts back if already present.
      --backend replay --id UNIT --verdicts FILE...: score from saved verdicts (dev/CI).

  haid bridge [--project PATH | --session FILE...] [--days N] [--out DIFF] [--show]
      The window -> (diff, usage) extractor: reconstruct the net code diff a window of real
      sessions produced (transcript replay, no git) + its normalized-token cost. Honesty
      caveats (incomplete files, excluded externals) are always printed. --out writes the
      diff; --show prints it. This is what feeds `haid value --project`.

  haid tag [--project PATH | --session FILE...] [--days N] [--json] [--backend {harness,replay}]
      The user-anchored pass, step 2: tag every user message with move × work-type + a
      one-sentence purpose snapshot (the purpose timeline that feeds episode segmentation).
      --backend harness (default): emit a per-message classification manifest for the host
        agent to run as subagents; reads labels back if already present.
      --backend replay --labels FILE...: label from saved fixtures (dev/CI, no model).

  haid episodes [--project PATH | --session FILE...] [--days N] [--json] --labels FILE...
      The user-anchored pass, step 3: group whole SESSIONS into EPISODES (the git-free PR proxy)
      by shared component/topic — the session is atomic, never subdivided. Needs the message
      labels from `haid tag` (--labels) for each session's purpose fingerprint.
      --backend heuristic (default): deterministic baseline — runs of sessions linked by files.
      --backend harness: emit one grouping manifest for the host agent; reads it back if present.
      --backend replay --grouping FILE: load a saved grouping (dev/CI, no model).

  haid score [--project PATH | --session FILE...] [--days N] --labels FILE... [--json]
      The why-pass step 4: score each EPISODE (a group of whole sessions) and report the window
      as a DISTRIBUTION of per-episode value, not one blended number. Per episode: episode-scope
      waste metrics + a reconstructed diff/cost (bridge over the episode's sessions) + difficulty/
      cleanliness placement → achievement and value. Placement is delegated to the host agent per
      episode/axis (emits manifests; re-run reads verdicts back). Grouping is the deterministic
      heuristic unless --grouping FILE is given.

  haid value (--diff PATH --usage PATH | --project PATH | --session FILE...) [options]
      Full fold: volume * difficulty * cleanliness = achievement; achievement / cost = value.
      Inputs come EITHER from explicit --diff/--usage, OR from real sessions via the bridge
      (--project/--session, optional --days). Places BOTH axes (harness emits two manifests;
      replay reads saved verdicts). Knobs: --alpha --top-ratio --gamma --floor.

  haid why [--project PATH | --session FILE...] [--days N] [--top N] [--model TIER] [--json]
      The why-pass, step 5: triage the window's top metric instances (by token weight, capped
      per metric; retries always considered) into investigation anchors, then delegate one
      tool-using analysis agent per anchor to the host agent. Each agent audits the anchor,
      explains why it happened with cited evidence, applies observable flags, and proposes a
      hedged remedy. Recommended agent tier: sonnet (--model overrides).
      --backend harness (default): emit why.job.json; re-run reads why.notes.json back.
      --backend replay --notes FILE...: saved notes (dev/CI, no model).

  haid report --metrics M.json [--why W.json] [--scores S.json] [--tags T.json] [options]
      The compositor: join the prior commands' --json outputs into (a) a DETERMINISTIC
      what/why digest (printed always; treatments matched mechanically from the shipped
      catalog) and (b) a composed coaching report via ONE opus-tier host-agent job
      (--backend harness emits compose.job.json; re-run reads the composition back;
      --backend replay --composition FILE for dev/CI). Recommendations are validated:
      they may only cite findings and treatments the deterministic layer produced.

  haid benchmark --scores S.json --github-user USER --project NAME [--out FILE]
      The ADR-0005 v1 self-reported submission row: summary statistics ONLY (leak check
      refuses paths/titles/session ids), ladder + combiner-config hashes, content hash.

  haid submit --scores S.json --github-user USER --project NAME [--repo PATH] [--dry-run] [--yes]
      Opt-in publish: build the row, show exactly what becomes PUBLIC + PERMANENT, then
      open a validated PR (git + gh) adding entries/<user>.json to the data-only benchmark
      repo. Identity = the GitHub PR author (no local signature, ADR-0005 v1). --dry-run
      writes the entry and prints the commands without pushing.

  haid rank --scores S.json [--github-user USER] [--board FILE | --refresh]
      Read-only: where your row lands against the community distribution (same ladders +
      combiner only). Reads the shipped board snapshot — uploads nothing. --refresh pulls
      the live board from Pages.

The live (harness) backend never calls a model in-process — it hands comparisons to the
host agent. See haid.scoring.compare.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, bridge, episodes, intent, report, viz, why, window
from .episodes import score as episode_score
from .metrics import json_out, view
from .scoring import cost, placement, value, volume
from .scoring.compare import HarnessBackend, PendingComparisons, ReplayBackend


def _cmd_metrics(args) -> int:
    if args.session:
        view_, sessions = window.from_files(args.session)
        project_path, days = None, None
    else:
        project_path = args.project or os.getcwd()
        days = args.days
        view_, sessions = window.for_project(project_path, days=days)
    doc = json_out.build(view_, sessions, project_path=project_path, days=days,
                         generated_at=datetime.now().isoformat(timespec="seconds"))
    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        print(view.render(doc, top_n=args.top_n))
    return 0


def _cmd_volume(args) -> int:
    res = volume.measure_file(args.diff)
    print(res.summary())
    return 0


def _cmd_cost(args) -> int:
    res = cost.measure_usage_file(args.usage)
    print(res.summary())
    return 0


def _cmd_place(args) -> int:
    diff = open(args.diff, encoding="utf-8").read()
    if args.backend == "replay":
        if not args.id or not args.verdicts:
            print("replay backend needs --id and --verdicts", file=sys.stderr)
            return 2
        backend = ReplayBackend.from_files(*args.verdicts)
        res = placement.place(diff, args.axis, backend, subject_id=args.id,
                              samples=args.samples)
    else:
        backend = HarnessBackend(job_dir=args.job_dir)
        try:
            res = placement.place(diff, args.axis, backend, samples=args.samples)
        except PendingComparisons as p:
            print(f"{p.n_jobs} comparisons pending.\n"
                  f"Run subagents over: {p.manifest_path}\n"
                  f"Write winners to:  {args.job_dir}/placement.verdicts.json "
                  '({"fingerprint": <manifest fingerprint>, "winners": ["A"|"B"|"tie", ...]}),'
                  " then re-run.")
            return 3

    tier = res.tier_label()
    print(f"axis={res.axis}  rung={res.rung:g}/{res.seen}  "
          f"percentile={res.percentile:.2f}" + (f"  [{tier}]" if tier else ""))
    return 0


def _bridge_for(args) -> bridge.BridgeResult:
    """Run the window->（diff, usage) bridge for --project or --session."""
    if args.session:
        view_, sessions = window.from_files(args.session)
    else:
        view_, sessions = window.for_project(args.project or os.getcwd(), days=args.days)
    return bridge.window_inputs(view_, sessions)


def _cmd_bridge(args) -> int:
    res = _bridge_for(args)
    print(res.summary())
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(res.diff)
        print(f"\n[diff written to {args.out}: {len(res.diff)} chars]")
    if args.show:
        print("\n--- reconstructed diff ---")
        print(res.diff)
    return 0


def _cmd_value(args) -> int:
    if args.project or args.session:
        br = _bridge_for(args)
        diff, cst = br.diff, br.cost
        if br.caveats:
            print("# bridge caveats:", file=sys.stderr)
            for c in br.caveats:
                print(f"#   {c}", file=sys.stderr)
        if not diff.strip():
            print("bridge produced an empty diff (no reconstructable changes in this window)",
                  file=sys.stderr)
            return 4
    elif args.diff and args.usage:
        diff = open(args.diff, encoding="utf-8").read()
        cst = cost.measure_usage_file(args.usage)
    else:
        print("value needs either --diff and --usage, or --project / --session",
              file=sys.stderr)
        return 2
    vol = volume.measure(diff)
    knobs = dict(alpha=args.alpha, top_ratio=args.top_ratio, gamma=args.gamma,
                 floor=args.floor)

    if args.backend == "replay":
        if not args.id or not args.verdicts:
            print("replay backend needs --id and --verdicts", file=sys.stderr)
            return 2
        backend = ReplayBackend.from_files(*args.verdicts)
        dpl = placement.place(diff, "difficulty", backend, subject_id=args.id,
                              samples=args.samples)
        cpl = placement.place(diff, "cleanliness", backend, subject_id=args.id,
                              samples=args.samples)
    else:
        pending = []
        placements = {}
        for axis in ("difficulty", "cleanliness"):
            be = HarnessBackend(job_dir=args.job_dir, job_name=axis)
            try:
                placements[axis] = placement.place(diff, axis, be, samples=args.samples)
            except PendingComparisons as p:
                pending.append((axis, p.manifest_path))
        if pending:
            for axis, mpath in pending:
                print(f"{axis}: run subagents over {mpath}, write winners (plus the "
                      f"manifest's fingerprint) to {args.job_dir}/{axis}.verdicts.json")
            print("then re-run.")
            return 3
        dpl, cpl = placements["difficulty"], placements["cleanliness"]

    ach = value.achievement(vol, dpl, cpl, **knobs)
    print(value.value(ach, cst).summary())
    return 0


def _cmd_tag(args) -> int:
    if args.session:
        view_, sessions = window.from_files(args.session)
        label = "explicit session list"
    else:
        project_path = args.project or os.getcwd()
        view_, sessions = window.for_project(project_path, days=args.days)
        label = view_.label

    if args.backend == "replay":
        if not args.labels:
            print("replay backend needs --labels", file=sys.stderr)
            return 2
        backend = intent.ReplayBackend.from_files(*args.labels)
    else:
        backend = intent.HarnessBackend(job_dir=args.job_dir)

    try:
        tagged = intent.tag_window(view_, sessions, backend)
    except intent.PendingClassifications as p:
        print(f"{p.n_jobs} classification job(s) — one haiku subagent per session branch.\n"
              f"Each job in {p.manifest_path} carries a branch transcript; the agent returns a "
              "`labels` array (one entry per marked message, echoing its uuid).\n"
              f"Aggregate every job's labels into:  {args.job_dir}/tag.labels.json "
              '({"labels": [{"uuid":…, "move":…, "work_type":…, "purpose":…}, …]}),'
              " then re-run.")
        return 3

    if args.json:
        print(json.dumps(intent.to_json(tagged, label=label), indent=2))
    else:
        print(intent.render(tagged, label=label))
    return 0


def _cmd_episodes(args) -> int:
    if args.session:
        view_, sessions = window.from_files(args.session)
        label = "explicit session list"
    else:
        project_path = args.project or os.getcwd()
        view_, sessions = window.for_project(project_path, days=args.days)
        label = view_.label

    if not args.labels:
        print("episodes needs --labels (the tag output from `haid tag`)", file=sys.stderr)
        return 2
    tagged = intent.tag_window(view_, sessions, intent.ReplayBackend.from_files(*args.labels))

    if args.backend == "replay":
        if not args.grouping:
            print("replay backend needs --grouping", file=sys.stderr)
            return 2
        backend = episodes.ReplayBackend.from_file(args.grouping)
    elif args.backend == "harness":
        backend = episodes.HarnessBackend(job_dir=args.job_dir)
    else:
        backend = episodes.HeuristicBackend()

    try:
        eps = episodes.segment_window(sessions, tagged, backend)
    except episodes.PendingSegmentation as p:
        print(f"Run the grouping agent over: {p.manifest_path}\n"
              f"Write the grouping to: {args.job_dir}/episodes.grouping.json "
              '({"episodes": [{"title":…, "session_ids":[…], "rationale":…}, …]}), then re-run.')
        return 3

    summaries = episodes.summarize.summarize_sessions(sessions, tagged)
    if args.json:
        print(json.dumps(episodes.to_json(eps, label=label), indent=2))
    else:
        print(episodes.render(eps, summaries=summaries, label=label))
    return 0


def _cmd_score(args) -> int:
    if args.session:
        view_, sessions = window.from_files(args.session)
        label = "explicit session list"
    else:
        project_path = args.project or os.getcwd()
        view_, sessions = window.for_project(project_path, days=args.days)
        label = view_.label

    if not args.labels:
        print("score needs --labels (the tag output from `haid tag`)", file=sys.stderr)
        return 2
    tagged = intent.tag_window(view_, sessions, intent.ReplayBackend.from_files(*args.labels))

    # Grouping into episodes: deterministic heuristic by default (stable episode ids across the
    # manifest-emit and read-back runs), or a saved grouping.
    if args.grouping:
        gbackend = episodes.ReplayBackend.from_file(args.grouping)
    else:
        gbackend = episodes.HeuristicBackend()
    try:
        eps = episodes.segment_window(sessions, tagged, gbackend)
    except episodes.PendingSegmentation as p:
        print(f"Group sessions first: run the grouping agent over {p.manifest_path}, then re-run.")
        return 3

    # Placement is delegated to the host agent per episode/axis (the live path). A future replay
    # path would key verdicts by episode id.
    def backend_for(axis: str, subject_id: str):
        return HarnessBackend(job_dir=args.job_dir, job_name=f"{subject_id}_{axis}")

    dist = episode_score.score_episodes(
        view_, sessions, eps, backend_for, samples=args.samples,
        alpha=args.alpha, top_ratio=args.top_ratio, gamma=args.gamma, floor=args.floor,
        label=label)

    if args.json:
        print(json.dumps(dist.to_json(), indent=2))
    else:
        print(dist.render())
    if dist.pending:
        print(f"\n{len(dist.pending)} placement manifest(s) under {args.job_dir}/ — run a subagent "
              "per job, write the verdicts beside each, then re-run.", file=sys.stderr)
        return 3
    return 0


def _cmd_why(args) -> int:
    if args.session:
        view_, sessions = window.from_files(args.session)
        label, project_path = "explicit session list", args.project or os.getcwd()
    else:
        project_path = args.project or os.getcwd()
        view_, sessions = window.for_project(project_path, days=args.days)
        label = view_.label
    if not sessions:
        print("no sessions in window", file=sys.stderr)
        return 2
    doc = json_out.build(view_, sessions, project_path=project_path, days=args.days,
                         generated_at=datetime.now().isoformat(timespec="seconds"))
    transcript_dir = str(Path(sessions[0].path).parent)

    anchors = why.select_anchors(doc, top=args.top, per_metric_cap=args.per_metric_cap)
    if not anchors:
        print("no anchors above threshold — nothing to investigate")
        return 0

    if args.backend == "replay":
        if not args.notes:
            print("replay backend needs --notes", file=sys.stderr)
            return 2
        backend = why.ReplayBackend.from_files(*args.notes)
    else:
        backend = why.HarnessBackend(job_dir=args.job_dir, model=args.model)

    try:
        results = why.investigate_window(doc, anchors, backend,
                                         transcript_dir=transcript_dir,
                                         project_path=project_path)
    except why.PendingInvestigations as p:
        print(f"{p.n_jobs} investigations pending.\n"
              f"Run one tool-using subagent per job in: {p.manifest_path}\n"
              f"(recommended model tier: {args.model}; each job's prompt is self-contained)\n"
              f"Write notes to: {args.job_dir}/why.notes.json "
              '({"notes": [{"anchor_id":…, <schema fields>}, …]}), then re-run.')
        return 3

    if args.json:
        print(json.dumps(why.to_json(results, label=label), indent=2))
    else:
        print(why.render(results, label=label))
    return 0


def _load_json(path: str | None) -> dict | None:
    return json.load(open(path, encoding="utf-8")) if path else None


def _community_block(scores_doc: dict, label: str, board_path: str | None) -> dict | None:
    """Rank this window against the community board for the report's context section.
    Placeholder identity — the report never needs a username; nothing is uploaded."""
    try:
        payload = report.benchmark.build_submission(
            scores_doc, github_username="you", project=(label or "(local)"),
            generated_at=datetime.now().isoformat(timespec="seconds"))
    except ValueError:
        return None
    board = (report.rank.load_board(board_path) if board_path
             else report.rank.shipped_board())
    return report.rank.rank_against(board, payload)


def _cmd_report(args) -> int:
    metrics_doc = _load_json(args.metrics)
    why_doc = _load_json(args.why)
    scores_doc = _load_json(args.scores)
    tags_doc = _load_json(args.tags)
    if not any((metrics_doc, why_doc, scores_doc, tags_doc)):
        print("report needs at least one input (--metrics/--why/--scores/--tags)",
              file=sys.stderr)
        return 2
    label = ((metrics_doc or {}).get("window", {}) or {}).get("label") \
        or (scores_doc or {}).get("window") or (tags_doc or {}).get("window") or ""
    catalog = report.load_catalog()
    findings = report.build_findings(why_doc=why_doc, tags_doc=tags_doc,
                                     scores_doc=scores_doc, catalog=catalog)
    community = _community_block(scores_doc, label, args.board) if scores_doc else None
    digest = report.digest_json(metrics_doc=metrics_doc, why_doc=why_doc,
                                scores_doc=scores_doc, tags_doc=tags_doc,
                                findings=findings, label=label, community=community)
    if args.digest_only:
        print(report.render_digest(digest))
        return 0

    if args.backend == "replay":
        if not args.composition:
            print("replay backend needs --composition", file=sys.stderr)
            return 2
        backend = report.ReplayBackend.from_file(args.composition)
    else:
        backend = report.HarnessBackend(job_dir=args.job_dir, model=args.model)
    try:
        comp = backend.compose(digest, findings, catalog)
    except report.PendingComposition as p:
        print(report.render_digest(digest))
        print(f"\n--\ncomposition pending: run ONE {args.model}-tier subagent over "
              f"{p.manifest_path}\nWrite its structured output to "
              f"{args.job_dir}/compose.composition.json, then re-run.", file=sys.stderr)
        return 3
    print(report.render_report(digest, comp, artifacts=_report_artifacts(args)))
    return 0


def _report_artifacts(args) -> dict:
    """{label: path} for the report's 'Where to look' footer — only paths that exist."""
    out: dict[str, str] = {}
    for label, path in (("metrics", args.metrics), ("scores", args.scores),
                        ("why", args.why), ("tags", args.tags)):
        if path and os.path.exists(path):
            out[label] = path
    if os.path.isdir(args.job_dir):
        out["job manifests"] = args.job_dir
    viz = os.path.join("viz", "index.html")
    if os.path.exists(viz):
        out["visualization"] = f"{viz} (open in a browser)"
    return out


def _cmd_benchmark(args) -> int:
    scores_doc = _load_json(args.scores)
    payload = report.benchmark.build_submission(
        scores_doc, github_username=args.github_user, project=args.project,
        generated_at=datetime.now().isoformat(timespec="seconds"))
    text = json.dumps(payload, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[submission payload written to {args.out}; "
              f"content_hash={payload['content_hash'][:16]}…]")
    else:
        print(text)
    return 0


def _build_payload(args, *, default_user: str) -> dict:
    scores_doc = _load_json(args.scores)
    return report.benchmark.build_submission(
        scores_doc, github_username=(args.github_user or default_user),
        project=args.project,
        generated_at=datetime.now().isoformat(timespec="seconds"))


def _cmd_submit(args) -> int:
    payload = _build_payload(args, default_user=args.github_user)
    repo_root = Path(args.repo) if args.repo else report.submit.find_repo_root()
    if repo_root is None:
        print("submit: no benchmark-repo checkout found nearby; pass --repo PATH "
              "(a local clone of the HAID repo)", file=sys.stderr)
        return 2
    print(report.submit.render_public_preview(payload))
    print()
    cmds = report.submit.pr_commands(args.github_user, args.project)
    if args.dry_run:
        dest = report.submit.write_entry(payload, repo_root)
        print(f"[dry-run] wrote entry: {dest}")
        print("[dry-run] would run, from the repo root:")
        for cmd in cmds:
            print("    " + " ".join(shlex.quote(c) for c in cmd))
        return 0
    if not args.yes:                      # this is PUBLIC + PERMANENT — require consent
        if not sys.stdin.isatty():
            print("submit: refusing to publish non-interactively without --yes",
                  file=sys.stderr)
            return 2
        if input("Publish this row to the PUBLIC community board? [y/N] ").strip().lower() \
                not in ("y", "yes"):
            print("aborted.")
            return 1
    report.submit.write_entry(payload, repo_root)
    try:
        url = report.submit.run_pr(repo_root, cmds)
    except RuntimeError as e:
        print(f"submit: {e}", file=sys.stderr)
        return 1
    print(f"submitted — PR opened: {url}")
    return 0


def _cmd_rank(args) -> int:
    if args.board:
        board = report.rank.load_board(args.board)
    elif args.refresh:
        board = report.rank.fetch_board(report.rank.BOARD_URL)
    else:
        board = report.rank.shipped_board()
    payload = _build_payload(args, default_user="you")
    ranking = report.rank.rank_against(board, payload)
    print(report.rank.render_rank(ranking, payload))
    return 0


def _cmd_viz(args) -> int:
    if args.session:
        view_, sessions = window.from_files(args.session)
        label, project_path = "explicit session list", args.project or os.getcwd()
    else:
        project_path = args.project or os.getcwd()
        view_, sessions = window.for_project(project_path, days=args.days)
        label = view_.label
    if not sessions:
        print("no sessions in window", file=sys.stderr)
        return 2

    session_dicts = [viz.extract_session(s, project_path=project_path) for s in sessions]
    empty = [d["stem"] for d in session_dicts if not d.get("spine")]

    scores_doc = _load_json(args.scores)
    grouping_doc = _load_json(args.grouping)
    metrics_doc = _load_json(args.metrics)
    bundle = viz.assemble_bundle(session_dicts, scores_doc=scores_doc,
                                 grouping_doc=grouping_doc, metrics_doc=metrics_doc,
                                 label=label)

    out_html = viz.write_html(bundle, args.out)
    note = {"scores": "real episodes + scores", "grouping": "real episodes (no scores)",
            "single_window": "no grouping artifact — one flat window episode"}
    print(f"[viz written to {out_html}]  {len(bundle['sessions'])} session(s), "
          f"{len(bundle['episodes'])} episode(s) ({note[bundle['episode_source']]})")
    if args.data_js:
        print(f"[data.js written to {viz.write_data_js(bundle, args.data_js)}]")
    if empty:
        print(f"# {len(empty)} session(s) had no active-timeline spine, skipped: "
              f"{', '.join(empty)}", file=sys.stderr)
    if bundle["episode_source"] == "single_window" and not args.session:
        print("# tip: pass --scores out/report/scores.json for the real episode grouping "
              "and per-episode score badges", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="haid", description="HAID scoring")
    # provenance: lets the report skill (and users) gate on the version that will compute —
    # a stale CLI shadowing the plugin silently produces wrong scores. Stamped in metrics.json
    # too (json_out.build), but a bare flag makes the preflight check trivial.
    p.add_argument("--version", action="version", version=f"haid {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    mt = sub.add_parser("metrics", help="waste metrics over an analysis window (the substrate)")
    mt.add_argument("--project", help="project path (default: cwd)")
    mt.add_argument("--days", type=int, default=30, help="window size in days (default 30)")
    mt.add_argument("--session", nargs="+",
                    help="explicit session JSONL path(s); overrides --project")
    mt.add_argument("--json", action="store_true", help="emit the JSON hand-off instead of Markdown")
    mt.add_argument("--top-n", type=int, default=10, help="instances shown per metric (Markdown)")
    mt.set_defaults(func=_cmd_metrics)

    v = sub.add_parser("volume", help="weighted surviving-LOC of a diff")
    v.add_argument("--diff", required=True)
    v.set_defaults(func=_cmd_volume)

    c = sub.add_parser("cost", help="normalized-token cost of a session (no $)")
    c.add_argument("--usage", required=True, help="usage JSON (see haid.scoring.cost)")
    c.set_defaults(func=_cmd_cost)

    pl = sub.add_parser("place", help="place a diff on a reference ladder")
    pl.add_argument("--diff", required=True)
    pl.add_argument("--axis", required=True, choices=["difficulty", "cleanliness"])
    pl.add_argument("--backend", default="harness", choices=["harness", "replay"])
    pl.add_argument("--samples", type=int, default=1)
    pl.add_argument("--id", help="subject unit id (replay backend)")
    pl.add_argument("--verdicts", nargs="+", help="saved verdict files (replay backend)")
    pl.add_argument("--job-dir", default="out/jobs", help="manifest dir (harness backend)")
    pl.set_defaults(func=_cmd_place)

    br = sub.add_parser("bridge", help="reconstruct a window's diff + cost (the scorer inputs)")
    br.add_argument("--project", help="project path (default: cwd)")
    br.add_argument("--days", type=int, default=30, help="window size in days (default 30)")
    br.add_argument("--session", nargs="+",
                    help="explicit session JSONL path(s); overrides --project")
    br.add_argument("--out", help="write the reconstructed diff to this file")
    br.add_argument("--show", action="store_true", help="print the reconstructed diff")
    br.set_defaults(func=_cmd_bridge)

    vv = sub.add_parser("value", help="fold volume*difficulty*cleanliness / cost -> value")
    vv.add_argument("--diff", help="explicit diff file (with --usage)")
    vv.add_argument("--usage", help="usage JSON (with --diff; see haid.scoring.cost)")
    vv.add_argument("--project", help="reconstruct inputs from a project's sessions (the bridge)")
    vv.add_argument("--days", type=int, default=30, help="window size in days (with --project)")
    vv.add_argument("--session", nargs="+", help="explicit session JSONL path(s) (the bridge)")
    vv.add_argument("--backend", default="harness", choices=["harness", "replay"])
    vv.add_argument("--samples", type=int, default=1)
    vv.add_argument("--id", help="subject unit id (replay backend)")
    vv.add_argument("--verdicts", nargs="+", help="saved verdict files (replay backend)")
    vv.add_argument("--job-dir", default="out/jobs", help="manifest dir (harness backend)")
    vv.add_argument("--alpha", type=float, default=value.DEFAULT_ALPHA, help="volume exponent")
    vv.add_argument("--top-ratio", type=float, default=value.DEFAULT_TOP_RATIO,
                    help="difficulty hardest/median multiple")
    vv.add_argument("--gamma", type=float, default=value.DEFAULT_GAMMA,
                    help="cleanliness penalty steepness")
    vv.add_argument("--floor", type=float, default=value.DEFAULT_FLOOR,
                    help="cleanliness anti-spam floor")
    vv.set_defaults(func=_cmd_value)

    tg = sub.add_parser("tag", help="tag user messages (move × work-type + purpose) — the why-pass step 2")
    tg.add_argument("--project", help="project path (default: cwd)")
    tg.add_argument("--days", type=int, default=30, help="window size in days (default 30)")
    tg.add_argument("--session", nargs="+",
                    help="explicit session JSONL path(s); overrides --project")
    tg.add_argument("--json", action="store_true", help="emit the JSON hand-off instead of Markdown")
    tg.add_argument("--backend", default="harness", choices=["harness", "replay"])
    tg.add_argument("--labels", nargs="+", help="saved label files (replay backend)")
    tg.add_argument("--job-dir", default="out/jobs", help="manifest dir (harness backend)")
    tg.set_defaults(func=_cmd_tag)

    ep = sub.add_parser("episodes", help="group sessions into episodes (the git-free PR proxy) — the why-pass step 3")
    ep.add_argument("--project", help="project path (default: cwd)")
    ep.add_argument("--days", type=int, default=30, help="window size in days (default 30)")
    ep.add_argument("--session", nargs="+",
                    help="explicit session JSONL path(s); overrides --project")
    ep.add_argument("--labels", nargs="+", required=False,
                    help="tag label file(s) from `haid tag` (per-session purpose fingerprints)")
    ep.add_argument("--json", action="store_true", help="emit the JSON hand-off instead of Markdown")
    ep.add_argument("--backend", default="heuristic", choices=["heuristic", "harness", "replay"],
                    help="grouping backend (default heuristic: deterministic baseline)")
    ep.add_argument("--grouping", help="saved grouping JSON (replay backend)")
    ep.add_argument("--job-dir", default="out/jobs", help="manifest dir (harness backend)")
    ep.set_defaults(func=_cmd_episodes)

    sc = sub.add_parser("score", help="per-EPISODE achievement/value distribution — the why-pass step 4")
    sc.add_argument("--project", help="project path (default: cwd)")
    sc.add_argument("--days", type=int, default=30, help="window size in days (default 30)")
    sc.add_argument("--session", nargs="+",
                    help="explicit session JSONL path(s); overrides --project")
    sc.add_argument("--labels", nargs="+", help="tag label file(s) from `haid tag`")
    sc.add_argument("--grouping", help="saved session grouping JSON (else deterministic heuristic)")
    sc.add_argument("--json", action="store_true", help="emit the JSON hand-off instead of Markdown")
    sc.add_argument("--samples", type=int, default=1, help="placement samples per axis (live variance)")
    sc.add_argument("--job-dir", default="out/jobs", help="placement manifest dir (host-agent path)")
    sc.add_argument("--alpha", type=float, default=value.DEFAULT_ALPHA, help="volume exponent")
    sc.add_argument("--top-ratio", type=float, default=value.DEFAULT_TOP_RATIO,
                    help="difficulty hardest/median multiple")
    sc.add_argument("--gamma", type=float, default=value.DEFAULT_GAMMA,
                    help="cleanliness penalty steepness")
    sc.add_argument("--floor", type=float, default=value.DEFAULT_FLOOR,
                    help="cleanliness anti-spam floor")
    sc.set_defaults(func=_cmd_score)

    wy = sub.add_parser("why", help="per-anchor investigation agents over the metrics — the why-pass step 5")
    wy.add_argument("--project", help="project path (default: cwd)")
    wy.add_argument("--days", type=int, default=30, help="window size in days (default 30)")
    wy.add_argument("--session", nargs="+",
                    help="explicit session JSONL path(s); overrides --project discovery")
    wy.add_argument("--top", type=int, default=why.DEFAULT_TOP,
                    help="max anchors to investigate (budget)")
    wy.add_argument("--per-metric-cap", type=int, default=why.DEFAULT_PER_METRIC_CAP,
                    help="max anchors per metric")
    wy.add_argument("--model", default=why.RECOMMENDED_MODEL,
                    help="recommended agent tier for the runner (default: sonnet)")
    wy.add_argument("--json", action="store_true", help="emit the JSON hand-off instead of Markdown")
    wy.add_argument("--backend", default="harness", choices=["harness", "replay"])
    wy.add_argument("--notes", nargs="+", help="saved note files (replay backend)")
    wy.add_argument("--job-dir", default="out/jobs", help="manifest dir (harness backend)")
    wy.set_defaults(func=_cmd_why)

    rp = sub.add_parser("report", help="compose the coaching report from prior --json outputs")
    rp.add_argument("--metrics", help="haid metrics --json output file")
    rp.add_argument("--why", help="haid why --json output file")
    rp.add_argument("--scores", help="haid score --json output file")
    rp.add_argument("--tags", help="haid tag --json output file")
    rp.add_argument("--digest-only", action="store_true",
                    help="print only the deterministic what/why digest (no model job)")
    rp.add_argument("--model", default=report.RECOMMENDED_MODEL,
                    help="recommended composition agent tier (default: opus)")
    rp.add_argument("--backend", default="harness", choices=["harness", "replay"])
    rp.add_argument("--composition", help="saved composition JSON (replay backend)")
    rp.add_argument("--job-dir", default="out/jobs", help="manifest dir (harness backend)")
    rp.add_argument("--board", help="local community board.json for the context section "
                    "(default: the shipped snapshot)")
    rp.set_defaults(func=_cmd_report)

    vz = sub.add_parser("viz", help="render the window visualization (self-contained HTML)")
    vz.add_argument("--project", help="project path (default: cwd)")
    vz.add_argument("--days", type=int, default=30, help="window size in days (default 30)")
    vz.add_argument("--session", nargs="+",
                    help="explicit session JSONL path(s); overrides --project")
    vz.add_argument("--scores", help="haid score --json output (real episodes + score badges)")
    vz.add_argument("--grouping", help="haid episodes grouping JSON (grouping without scores)")
    vz.add_argument("--metrics", help="haid metrics --json output (file-flag overlay)")
    vz.add_argument("--out", default="out/report/haid-viz.html",
                    help="self-contained HTML destination (default out/report/haid-viz.html)")
    vz.add_argument("--data-js", help="also write the dev data.js bundle here (e.g. viz/data.js)")
    vz.set_defaults(func=_cmd_viz)

    bm = sub.add_parser("benchmark", help="build the ADR-0005 v1 submission payload (summary only)")
    bm.add_argument("--scores", required=True, help="haid score --json output file")
    bm.add_argument("--github-user", required=True, help="GitHub username (entry identity)")
    bm.add_argument("--project", required=True, help="project display name")
    bm.add_argument("--out", help="write the payload to this file")
    bm.set_defaults(func=_cmd_benchmark)

    sb = sub.add_parser("submit",
                        help="opt-in: open a PR adding your summary row to the community board")
    sb.add_argument("--scores", required=True, help="haid score --json output file")
    sb.add_argument("--github-user", required=True,
                    help="your GitHub username (entry identity == PR author)")
    sb.add_argument("--project", required=True, help="project display name")
    sb.add_argument("--repo", help="local checkout of the benchmark repo (default: auto-detect)")
    sb.add_argument("--dry-run", action="store_true",
                    help="write the entry + print the git/gh commands; push nothing")
    sb.add_argument("--yes", action="store_true",
                    help="skip the interactive confirmation (publishes immediately)")
    sb.set_defaults(func=_cmd_submit)

    rk = sub.add_parser("rank",
                        help="read-only: where your scores land vs the community (uploads nothing)")
    rk.add_argument("--scores", required=True, help="haid score --json output file")
    rk.add_argument("--github-user", help="your GitHub username (to exclude your own prior row)")
    rk.add_argument("--project", default="(local)", help="project display name")
    rk.add_argument("--board", help="local board.json (default: the shipped snapshot)")
    rk.add_argument("--refresh", action="store_true",
                    help="fetch the live board from Pages instead of the shipped snapshot")
    rk.set_defaults(func=_cmd_rank)
    return p


def main(argv: list[str] | None = None) -> int:
    try:                              # emit UTF-8 even on a cp1252 Windows console
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                 # noqa: BLE001 — not a TextIOWrapper (piped/redirected)
        pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
