"""Generate the joined pilot data + the discriminating correlations for the writeup."""
import json
import math
import calibration.bt_h5 as bt

verdicts = json.load(open("out/verdicts.json", encoding="utf-8"))["verdicts"]
idx = {u["id"]: u for u in (json.loads(l) for l in
       open("out/units_blinded.jsonl", encoding="utf-8") if l.strip())}
ids = list(idx)

strength = bt.fit_bradley_terry(ids, verdicts)
latent = {i: math.log(strength[i]) for i in ids}
order = sorted(ids, key=lambda i: -latent[i])

def sig(i, k): return idx[i]["review_signals"].get(k)
def churn(i): return (idx[i].get("additions") or 0) + (idx[i].get("deletions") or 0)

print("RANK  unit  oracle  prior  churn  revs  commits  ttm(h)  chg_req  rcmts  repo")
for r, i in enumerate(order, 1):
    print(f"{r:>3}  {i}  {latent[i]:+5.2f}  {idx[i]['difficulty_prior']:>4}  "
          f"{churn(i):>5}  {sig(i,'num_reviewers')}  {sig(i,'commits'):>5}  "
          f"{(sig(i,'time_to_merge_hours') or 0):>7.1f}  {sig(i,'changes_requested')}  "
          f"{sig(i,'review_comments')}  {idx[i]['repo']}#{idx[i]['number']}")

prior_rank = {"low": 0, "mid": 1, "high": 2}
oracle = [latent[i] for i in ids]
prior = [prior_rank.get(idx[i]["difficulty_prior"], 1) for i in ids]
size = [churn(i) for i in ids]
comp = bt._composite(idx, ids) if hasattr(bt, "_composite") else None

def col(k): return [float(sig(i, k) or 0) for i in ids]

print("\n--- correlation matrix (Spearman) ---")
pairs = {
    "oracle vs review-COMPOSITE": (oracle, comp),
    "oracle vs commits": (oracle, col("commits")),
    "oracle vs num_reviewers": (oracle, col("num_reviewers")),
    "oracle vs difficulty_prior": (oracle, prior),
    "oracle vs churn(size)": (oracle, size),
    "PRIOR vs review-COMPOSITE (oracle-independent!)": (prior, comp),
    "PRIOR vs commits": (prior, col("commits")),
    "review-COMPOSITE vs churn(size)": (comp, size),
    "commits vs churn(size)": (col("commits"), size),
    "num_reviewers vs churn(size)": (col("num_reviewers"), size),
}
for name, (x, y) in pairs.items():
    print(f"  {name:>48}: rho={bt.spearman(x, y):+.3f}")
