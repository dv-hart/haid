"""Pull ONE real, content-trimmed example of each key record/tool from the real
transcripts, plus a contiguous slice showing the info->action->change loop.
Long strings are truncated so the output is readable and avoids dumping blobs.
Writes research/_examples.json and prints it."""
import json, copy
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
SELECT = ("DataVine", "software")
MAXSTR = 140      # truncate any string longer than this
MAXLIST = 4       # keep at most this many items of a long list

def dirs():
    for d in sorted(PROJECTS.iterdir()):
        if d.is_dir() and any(s in d.name for s in SELECT):
            yield d

def load(p):
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except: pass
    return out

def trim(o, depth=0):
    if isinstance(o, str):
        return o if len(o) <= MAXSTR else o[:MAXSTR] + f"…[+{len(o)-MAXSTR} chars]"
    if isinstance(o, list):
        t = [trim(x, depth+1) for x in o[:MAXLIST]]
        if len(o) > MAXLIST:
            t.append(f"…[+{len(o)-MAXLIST} more items]")
        return t
    if isinstance(o, dict):
        return {k: trim(v, depth+1) for k, v in o.items()}
    return o

def blocks(rec):
    m = rec.get("message")
    if isinstance(m, dict) and isinstance(m.get("content"), list):
        return [b for b in m["content"] if isinstance(b, dict)]
    return []

def has_tool(rec, name):
    return any(b.get("type") == "tool_use" and b.get("name") == name for b in blocks(rec))

examples = {}
slice_out = None

# gather all main sessions, richest project first
sessions = []
for d in dirs():
    for jp in sorted(d.glob("*.jsonl")):
        sessions.append(jp)

def want(key):
    return key not in examples

for jp in sessions:
    recs = load(jp)
    id2tool = {}
    for i, r in enumerate(recs):
        if not isinstance(r, dict): continue
        t = r.get("type")
        for b in blocks(r):
            if b.get("type") == "tool_use" and b.get("id"):
                id2tool[b["id"]] = b.get("name")
        # user instruction (string content, not a tool result, not meta)
        if want("user_instruction") and t == "user" and "toolUseResult" not in r \
           and isinstance(r.get("message", {}).get("content"), str) \
           and not r.get("isMeta") and not r.get("isCompactSummary"):
            examples["user_instruction"] = trim(r)
        # assistant with thinking+text+tool_use
        if want("assistant_full") and t == "assistant":
            bt = {b.get("type") for b in blocks(r)}
            if "tool_use" in bt and ("thinking" in bt or "text" in bt):
                examples["assistant_full"] = trim(r)
        # tool results by tool
        tur = r.get("toolUseResult")
        if isinstance(tur, dict):
            nm = "?"
            for b in blocks(r):
                if b.get("type") == "tool_result":
                    nm = id2tool.get(b.get("tool_use_id"), "?"); break
            key = f"result_{nm}"
            if want(key) and nm in ("Read","Edit","Write","Bash","Grep","Glob","Agent","WebSearch"):
                examples[key] = trim(r)
        # specific tool_use blocks (input side)
        for b in blocks(r):
            if b.get("type") == "tool_use":
                k = f"call_{b.get('name')}"
                if want(k) and b.get("name") in ("Read","Edit","Write","Bash","Grep","Agent"):
                    examples[k] = trim({"type":"assistant.tool_use", **b})
        # compaction
        if want("compact_boundary") and r.get("subtype") == "compact_boundary":
            examples["compact_boundary"] = trim(r)
        if want("compact_summary") and t == "user" and r.get("isCompactSummary"):
            examples["compact_summary"] = trim(r)
        # attachment
        if t == "attachment":
            a = r.get("attachment", {})
            k = f"attachment_{a.get('type','?')}"
            if want(k):
                examples[k] = trim(r)
        # system hook
        if want("system_hook") and r.get("subtype") == "stop_hook_summary":
            examples["system_hook"] = trim(r)
        if want("mode") and t == "mode":
            examples["mode"] = trim(r)

# contiguous slice: find user-instruction -> ... -> an Edit result, dump ~7 recs
for jp in sessions:
    recs = load(jp)
    for i in range(len(recs)-1):
        r = recs[i]
        if isinstance(r, dict) and r.get("type") == "user" \
           and isinstance(r.get("message", {}).get("content"), str) \
           and "toolUseResult" not in r and not r.get("isMeta"):
            window = recs[i:i+8]
            if any(isinstance(w, dict) and w.get("toolUseResult") for w in window):
                def mini(rec):
                    out = {"type": rec.get("type"),
                           "uuid": (rec.get("uuid") or "")[:8],
                           "parentUuid": (rec.get("parentUuid") or "")[:8],
                           "ts": rec.get("timestamp")}
                    bl = blocks(rec)
                    if bl:
                        out["blocks"] = []
                        for b in bl:
                            bt = b.get("type")
                            if bt == "tool_use":
                                out["blocks"].append({"tool_use": b.get("name"),
                                                      "id": (b.get("id") or "")[:10],
                                                      "input_keys": list((b.get("input") or {}).keys())})
                            elif bt == "tool_result":
                                out["blocks"].append({"tool_result_for": (b.get("tool_use_id") or "")[:10]})
                            else:
                                txt = b.get(bt, "")
                                out["blocks"].append({bt: (txt[:80]+"…") if isinstance(txt,str) and len(txt)>80 else txt})
                    if isinstance(rec.get("message",{}).get("content"), str):
                        c = rec["message"]["content"]
                        out["text"] = (c[:80]+"…") if len(c)>80 else c
                    if rec.get("toolUseResult") is not None:
                        tur = rec["toolUseResult"]
                        out["toolUseResult_keys"] = list(tur.keys()) if isinstance(tur,dict) else f"<{type(tur).__name__}>"
                    if rec.get("sourceToolUseID"):
                        out["sourceToolUseID"] = rec["sourceToolUseID"][:10]
                    return out
                slice_out = [mini(w) for w in window if isinstance(w, dict)]
                break
    if slice_out: break

result = {"examples": examples, "contiguous_slice": slice_out}
Path(__file__).with_name("_examples.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8")
print("KEYS:", sorted(examples.keys()))
print("\n=== CONTIGUOUS SLICE ===")
print(json.dumps(slice_out, indent=2))
