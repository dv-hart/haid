"""Deep-dive probe: exact input keys per tool, structured toolUseResult schema
per tool, full field inventory per record type, and concrete (content-free)
samples for compaction + key tools. Reads from the same projects."""
import json, collections
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
SELECT = ("DataVine", "software")

def dirs():
    for d in sorted(PROJECTS.iterdir()):
        if d.is_dir() and any(s in d.name for s in SELECT):
            yield d

def recs(p):
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try: yield json.loads(line)
                except: pass

def blocks(rec):
    m = rec.get("message")
    if isinstance(m, dict) and isinstance(m.get("content"), list):
        yield from (b for b in m["content"] if isinstance(b, dict))

def keyshape(d):
    """{key: typename} for a dict, recursing one level for dicts."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = "dict{" + ",".join(list(v.keys())[:8]) + "}"
        elif isinstance(v, list):
            inner = v[0] if v else None
            out[k] = "list[" + (("dict{" + ",".join(list(inner.keys())[:6]) + "}") if isinstance(inner, dict) else type(inner).__name__) + "]"
        else:
            out[k] = type(v).__name__
    return out

tur_keys_by_tool = collections.defaultdict(collections.Counter)
tur_sample_by_tool = {}
input_sample_by_tool = {}
input_keys_by_tool = collections.defaultdict(collections.Counter)
compact_samples = []
fields_by_type = collections.defaultdict(collections.Counter)
user_msg_content_shapes = collections.Counter()

for d in dirs():
    for jp in list(d.glob("*.jsonl")) + list(d.glob("*/subagents/*.jsonl")):
        id2tool = {}
        for r in recs(jp):
            if not isinstance(r, dict): continue
            t = r.get("type", "?")
            for k in r: fields_by_type[t][k] += 1
            # compaction deep sample
            if r.get("subtype") == "compact_boundary" and len(compact_samples) < 3:
                cm = r.get("compactMetadata")
                compact_samples.append({
                    "keys": list(r.keys()),
                    "compactMetadata": cm if isinstance(cm, (dict, str, int)) else keyshape(cm) if isinstance(cm, dict) else str(type(cm)),
                    "content_shape": type(r.get("content")).__name__,
                    "logicalParentUuid": bool(r.get("logicalParentUuid")),
                })
            if t == "assistant":
                for b in blocks(r):
                    if b.get("type") == "tool_use":
                        nm = b.get("name", "?")
                        if b.get("id"): id2tool[b["id"]] = nm
                        inp = b.get("input", {})
                        if isinstance(inp, dict):
                            for k in inp: input_keys_by_tool[nm][k] += 1
                            if nm not in input_sample_by_tool:
                                input_sample_by_tool[nm] = keyshape(inp)
            if t == "user":
                m = r.get("message", {})
                if isinstance(m, dict):
                    c = m.get("content")
                    user_msg_content_shapes[type(c).__name__] += 1
            tur = r.get("toolUseResult")
            if tur is not None:
                nm = "?"
                for b in blocks(r):
                    if b.get("type") == "tool_result":
                        nm = id2tool.get(b.get("tool_use_id"), "?"); break
                if isinstance(tur, dict):
                    for k in tur: tur_keys_by_tool[nm][k] += 1
                    if nm not in tur_sample_by_tool:
                        tur_sample_by_tool[nm] = keyshape(tur)
                else:
                    tur_keys_by_tool[nm][f"<{type(tur).__name__}>"] += 1

P = lambda *a: print(*a)
P("=== INPUT KEYS per tool (what every tool call deterministically carries) ===")
for nm in ["Read","Edit","Write","MultiEdit","Bash","PowerShell","Grep","Glob","Agent","TaskCreate","TaskUpdate","WebSearch","WebFetch"]:
    if nm in input_keys_by_tool:
        P(f"  {nm}: {dict(input_keys_by_tool[nm].most_common())}")
P("\n=== INPUT sample (key->type) ===")
for nm in ["Read","Edit","Write","Bash","Grep","Agent"]:
    if nm in input_sample_by_tool: P(f"  {nm}: {input_sample_by_tool[nm]}")

P("\n=== toolUseResult KEYS per tool (structured result payload) ===")
for nm, c in sorted(tur_keys_by_tool.items(), key=lambda x:-sum(x[1].values())):
    P(f"  {nm}: {dict(c.most_common())}")
P("\n=== toolUseResult sample (key->type) ===")
for nm in ["Read","Edit","Write","Bash","Grep","Glob","TodoWrite","Agent"]:
    if nm in tur_sample_by_tool: P(f"  {nm}: {tur_sample_by_tool[nm]}")

P("\n=== user message content shape ===", dict(user_msg_content_shapes))
P("\n=== COMPACTION samples ===")
for s in compact_samples: P(" ", s)

P("\n=== FIELD INVENTORY per record type ===")
for t in ["user","assistant","system","attachment","mode","queue-operation","custom-title","ai-title","last-prompt"]:
    if t in fields_by_type:
        P(f"  {t}: {list(fields_by_type[t].keys())}")
