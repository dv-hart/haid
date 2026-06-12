"""
HAID Phase-0 record analysis.

Walks every Claude Code project under ~/.claude/projects that matches the
DataVine / software workspaces, parses all transcripts (main + subagents),
and emits a structural/statistical breakdown — NO message content, only shapes,
field frequencies, and tool/result schemas. Writes full detail to
research/_analysis.json and prints a digest.

Run: python research/analyze_records.py
"""
import json, os, glob, collections, statistics
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
# match the workspaces the maintainer pointed at
SELECT = ("DataVine", "software")

def iter_project_dirs():
    for d in sorted(PROJECTS.iterdir()):
        if d.is_dir() and any(s in d.name for s in SELECT):
            yield d

def load_jsonl(path):
    recs = []
    try:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    recs.append({"__parse_error__": True, "__line__": i})
    except Exception as e:
        return [], str(e)
    return recs, None

def msg_content_blocks(rec):
    """Yield content blocks from a message-bearing record."""
    m = rec.get("message")
    if isinstance(m, dict):
        c = m.get("content")
        if isinstance(c, list):
            yield from (b for b in c if isinstance(b, dict))

def shape(v):
    if isinstance(v, dict): return "dict"
    if isinstance(v, list): return "list"
    if isinstance(v, str):  return "str"
    if isinstance(v, bool): return "bool"
    if isinstance(v, (int, float)): return "num"
    if v is None: return "null"
    return type(v).__name__

def main():
    report = {
        "projects": {},
        "versions": collections.Counter(),
        "record_types": collections.Counter(),
        "fields_by_type": collections.defaultdict(collections.Counter),
        "tool_use_names": collections.Counter(),
        "tool_input_keys": collections.defaultdict(collections.Counter),
        "tooluseresult_shape": collections.Counter(),       # str/dict/list per occurrence
        "tooluseresult_keys_by_tool": collections.defaultdict(collections.Counter),
        "usage_keys": collections.Counter(),
        "assistant_content_block_types": collections.Counter(),
        "attachment_subtypes": collections.Counter(),
        "system_subtypes": collections.Counter(),
        "compaction_hits": [],         # any record smelling of compaction
        "branching": {"parents_with_multiple_children": 0, "total_parents": 0},
        "sidechain": collections.Counter(),
        "subagent_meta_keys": collections.Counter(),
        "subagent_meta_sample": None,
        "output_token_samples": [],    # for distribution
        "edit_tools_seen": collections.Counter(),
        "stop_reasons": collections.Counter(),
    }

    all_sessions = 0
    for pdir in iter_project_dirs():
        main_jsonls = sorted(pdir.glob("*.jsonl"))
        sub_jsonls = sorted(pdir.glob("*/subagents/*.jsonl"))
        metas = sorted(pdir.glob("*/subagents/*.meta.json"))
        toolresult_files = sorted(pdir.glob("*/tool-results/*"))
        pinfo = {
            "name": pdir.name,
            "main_sessions": len(main_jsonls),
            "subagent_transcripts": len(sub_jsonls),
            "subagent_metas": len(metas),
            "tool_result_overflow_files": len(toolresult_files),
            "total_bytes": sum(p.stat().st_size for p in main_jsonls + sub_jsonls),
            "record_count": 0,
        }
        # subagent meta sample
        for mp in metas[:1]:
            try:
                md = json.loads(Path(mp).read_text(encoding="utf-8"))
                for k in md: report["subagent_meta_keys"][k] += 1
                if report["subagent_meta_sample"] is None:
                    report["subagent_meta_sample"] = {k: shape(v) for k, v in md.items()}
            except Exception:
                pass
        for mp in metas[1:]:
            try:
                md = json.loads(Path(mp).read_text(encoding="utf-8"))
                for k in md: report["subagent_meta_keys"][k] += 1
            except Exception:
                pass

        for jp in main_jsonls + sub_jsonls:
            all_sessions += 1
            recs, err = load_jsonl(jp)
            if err:
                continue
            pinfo["record_count"] += len(recs)
            # tool_use id -> name map (for attributing tool results)
            id2tool = {}
            children = collections.Counter()
            for rec in recs:
                if not isinstance(rec, dict):
                    continue
                t = rec.get("type", "__none__")
                report["record_types"][t] += 1
                for k in rec.keys():
                    report["fields_by_type"][t][k] += 1
                if "version" in rec:
                    report["versions"][rec["version"]] += 1
                if "isSidechain" in rec:
                    report["sidechain"][bool(rec["isSidechain"])] += 1
                pu = rec.get("parentUuid")
                if pu:
                    children[pu] += 1
                # compaction smell
                low = json.dumps(rec)[:400].lower()
                if t == "summary" or "compact" in low or "iscompactsummary" in low:
                    if len(report["compaction_hits"]) < 8:
                        report["compaction_hits"].append({
                            "project": pdir.name, "file": jp.name, "type": t,
                            "keys": list(rec.keys())[:20],
                        })
                if t == "attachment":
                    a = rec.get("attachment")
                    if isinstance(a, dict):
                        report["attachment_subtypes"][a.get("type", "?")] += 1
                if t == "system":
                    report["system_subtypes"][rec.get("subtype", "?")] += 1
                if t == "assistant":
                    msg = rec.get("message", {})
                    if isinstance(msg, dict):
                        if "stop_reason" in msg:
                            report["stop_reasons"][msg.get("stop_reason")] += 1
                        u = msg.get("usage")
                        if isinstance(u, dict):
                            for k in u: report["usage_keys"][k] += 1
                            ot = u.get("output_tokens")
                            if isinstance(ot, int) and len(report["output_token_samples"]) < 5000:
                                report["output_token_samples"].append(ot)
                    for b in msg_content_blocks(rec):
                        bt = b.get("type", "?")
                        report["assistant_content_block_types"][bt] += 1
                        if bt == "tool_use":
                            name = b.get("name", "?")
                            report["tool_use_names"][name] += 1
                            tid = b.get("id")
                            if tid: id2tool[tid] = name
                            inp = b.get("input", {})
                            if isinstance(inp, dict):
                                for k in inp:
                                    report["tool_input_keys"][name][k] += 1
                            if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                                report["edit_tools_seen"][name] += 1
                # tool results (ride on user records)
                tur = rec.get("toolUseResult")
                if tur is not None:
                    report["tooluseresult_shape"][shape(tur)] += 1
                    # find the tool_use_id from content block
                    tool_name = "?"
                    for b in msg_content_blocks(rec):
                        if b.get("type") == "tool_result":
                            tid = b.get("tool_use_id")
                            tool_name = id2tool.get(tid, "?")
                            break
                    if isinstance(tur, dict):
                        for k in tur:
                            report["tooluseresult_keys_by_tool"][tool_name][k] += 1
            # branching stats
            report["branching"]["total_parents"] += len(children)
            report["branching"]["parents_with_multiple_children"] += sum(
                1 for c in children.values() if c > 1)
        report["projects"][pdir.name] = pinfo

    # ---- finalize / serialize ----
    def dictify(o):
        if isinstance(o, collections.Counter):
            return dict(o.most_common())
        if isinstance(o, collections.defaultdict):
            return {k: dictify(v) for k, v in o.items()}
        if isinstance(o, dict):
            return {k: dictify(v) for k, v in o.items()}
        return o
    out = {k: dictify(v) for k, v in report.items()}
    # token distribution summary
    toks = report["output_token_samples"]
    if toks:
        toks_sorted = sorted(toks)
        out["output_token_dist"] = {
            "n": len(toks), "min": toks_sorted[0], "max": toks_sorted[-1],
            "median": statistics.median(toks_sorted),
            "p90": toks_sorted[int(len(toks_sorted)*0.9)],
            "mean": round(statistics.mean(toks_sorted), 1),
        }
    out.pop("output_token_samples", None)

    dest = Path(__file__).with_name("_analysis.json")
    dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    # ---- digest ----
    print(f"sessions parsed (main+sub): {all_sessions}")
    print(f"\n== projects ==")
    for n, p in out["projects"].items():
        print(f"  {n}")
        print(f"     main={p['main_sessions']} sub={p['subagent_transcripts']} "
              f"overflow={p['tool_result_overflow_files']} recs={p['record_count']} "
              f"MB={p['total_bytes']/1e6:.1f}")
    print(f"\n== versions ==\n  {out['versions']}")
    print(f"\n== record types ==\n  {out['record_types']}")
    print(f"\n== tool_use names ==\n  {out['tool_use_names']}")
    print(f"\n== assistant content block types ==\n  {out['assistant_content_block_types']}")
    print(f"\n== usage keys ==\n  {out['usage_keys']}")
    print(f"\n== stop reasons ==\n  {out['stop_reasons']}")
    print(f"\n== toolUseResult shape ==\n  {out['tooluseresult_shape']}")
    print(f"\n== attachment subtypes ==\n  {out['attachment_subtypes']}")
    print(f"\n== system subtypes ==\n  {out['system_subtypes']}")
    print(f"\n== sidechain ==\n  {out['sidechain']}")
    print(f"\n== branching ==\n  {out['branching']}")
    print(f"\n== compaction hits ({len(out['compaction_hits'])}) ==")
    for h in out["compaction_hits"]:
        print(f"     {h['type']:14} {h['file'][:20]} keys={h['keys']}")
    print(f"\n== subagent meta keys ==\n  {out['subagent_meta_keys']}")
    print(f"  sample shapes: {out['subagent_meta_sample']}")
    print(f"\n== output token dist ==\n  {out.get('output_token_dist')}")
    print(f"\nfull detail -> {dest}")

if __name__ == "__main__":
    main()
