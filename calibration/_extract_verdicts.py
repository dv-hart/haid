"""One-off: pull the workflow result's verdicts into out/verdicts.json."""
import json
import sys

src = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else "out/verdicts.json"
data = json.load(open(src, encoding="utf-8"))
result = data.get("result", data)
json.dump(result, open(out, "w", encoding="utf-8"))
v = result["verdicts"]
ties = sum(1 for x in v if x["winner"] == "tie")
print(f"wrote {len(v)} verdicts to {out} ({ties} ties)")
