"""Bradley-Terry fit + the H5 gate (docs/calibration-experiment.md §4a, §7).

Takes the oracle's pairwise verdicts, fits a Bradley-Terry latent *difficulty* score
per unit (robust to the noisy, non-transitive individual calls a comparison sort
would choke on), then correlates that ranking against the INDEPENDENT mined review
signals. That correlation is H5: if it holds, the oracle is a trustworthy ground
truth; if not, we do not build an SEH scale on it.

Stdlib only (no numpy/scipy).

verdict format (out/verdicts.json): {"axis": "...", "verdicts": [{"a","b","winner"}]}
  winner is an id (a or b) or "tie".
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict


def fit_bradley_terry(ids: list[str], verdicts: list[dict],
                      iters: int = 300, eps: float = 1e-9,
                      alpha: float = 1.0) -> dict[str, float]:
    """Regularized MM Bradley-Terry. Ties = half a win each.

    `alpha` adds a weak prior: each item plays `alpha` virtual games split 50/50
    against a reference opponent of strength 1. This keeps total-losers/total-winners
    finite instead of pinning them to the eps floor (the k=3 degeneracy). Returns
    strengths p_i (geometric mean 1); latent score = ln(p_i).
    """
    idset = set(ids)
    wins: dict[str, float] = defaultdict(float)
    pair_n: dict[tuple[str, str], int] = defaultdict(int)
    for v in verdicts:
        a, b, w = v["a"], v["b"], v["winner"]
        if a not in idset or b not in idset:
            continue
        pair_n[_key(a, b)] += 1
        if w == "tie":
            wins[a] += 0.5
            wins[b] += 0.5
        elif w == a:
            wins[a] += 1.0
        elif w == b:
            wins[b] += 1.0

    p = {i: 1.0 for i in ids}
    for _ in range(iters):
        new = {}
        for i in ids:
            denom = alpha / (p[i] + 1.0)          # prior: alpha games vs strength-1 ref
            for j in ids:
                if j == i:
                    continue
                n = pair_n.get(_key(i, j), 0)
                if n:
                    denom += n / (p[i] + p[j])
            new[i] = (wins[i] + alpha * 0.5) / denom if denom > eps else p[i]
        logmean = sum(math.log(max(v, eps)) for v in new.values()) / len(new)
        scale = math.exp(logmean)
        p = {i: max(new[i] / scale, eps) for i in ids}
    return p


def oracle_consistency(ids: list[str], verdicts: list[dict],
                       strength: dict[str, float]) -> float:
    """Fraction of decisive comparisons the fitted ranking agrees with.

    ~0.5 = oracle is noise (nothing to validate). High (>~0.7) = oracle is
    self-consistent, so a weak H5 implicates the review-signal proxy, not the oracle.
    """
    agree = total = 0
    for v in verdicts:
        a, b, w = v["a"], v["b"], v["winner"]
        if w == "tie" or a not in strength or b not in strength:
            continue
        total += 1
        pred = a if strength[a] >= strength[b] else b
        if pred == w:
            agree += 1
    return agree / total if total else float("nan")


def _key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


# -- rank correlation (stdlib Spearman) -------------------------------------------
def rankdata(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0           # average rank (1-based)
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = math.sqrt(sum((a - mx) ** 2 for a in x))
    vy = math.sqrt(sum((b - my) ** 2 for b in y))
    return cov / (vx * vy) if vx > 0 and vy > 0 else float("nan")


def spearman(x: list[float], y: list[float]) -> float:
    return pearson(rankdata(x), rankdata(y))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bradley-Terry fit + H5 correlation gate")
    p.add_argument("--verdicts", default="out/verdicts.json")
    p.add_argument("--index", default="out/units_blinded.jsonl")
    args = p.parse_args(argv)

    data = json.load(open(args.verdicts, encoding="utf-8"))
    verdicts = data["verdicts"]
    index = {u["id"]: u for u in
             (json.loads(l) for l in open(args.index, encoding="utf-8") if l.strip())}
    ids = list(index.keys())

    strength = fit_bradley_terry(ids, verdicts)
    latent = {i: math.log(strength[i]) for i in ids}
    consistency = oracle_consistency(ids, verdicts, strength)

    print(f"=== oracle difficulty ranking (Bradley-Terry over {len(verdicts)} "
          f"verdicts, {len(ids)} units) ===")
    print(f"oracle self-consistency: {consistency:.1%} of comparisons agree with the "
          f"fitted ranking  (~50% = noise, >70% = coherent)\n")
    for i in sorted(ids, key=lambda x: -latent[x]):
        u = index[i]
        s = u["review_signals"]
        print(f"  {i}  score={latent[i]:+.2f}  [{u.get('difficulty_prior')}/"
              f"{u.get('volume_prior')}]  {u['repo']}#{u['number']}  "
              f"reviewers={s.get('num_reviewers')} ttm={s.get('time_to_merge_hours')}h")

    # --- H5: oracle ranking vs independent review signals ---
    def col(name, fn):
        xs, ys = [], []
        for i in ids:
            val = fn(index[i])
            if val is not None:
                xs.append(latent[i]); ys.append(float(val))
        return xs, ys

    signals = {
        "num_reviewers": lambda u: u["review_signals"].get("num_reviewers"),
        "time_to_merge_hours": lambda u: u["review_signals"].get("time_to_merge_hours"),
        "changes_requested": lambda u: u["review_signals"].get("changes_requested"),
        "commits": lambda u: u["review_signals"].get("commits"),
        "review_comments": lambda u: u["review_signals"].get("review_comments"),
    }
    # composite review-effort = sum of per-signal ranks (more review work = higher)
    print("\n=== H5: Spearman(oracle difficulty, review signal) ===")
    for name, fn in signals.items():
        xs, ys = col(name, fn)
        if len(xs) >= 5:
            print(f"  {name:>22}: rho={spearman(xs, ys):+.3f}  (n={len(xs)})")

    comp_ids = [i for i in ids]
    comp_vals = _composite(index, comp_ids)
    rho = spearman([latent[i] for i in comp_ids], comp_vals)
    print(f"  {'COMPOSITE review-effort':>22}: rho={rho:+.3f}  (n={len(comp_ids)})")

    # sanity references (not H5): does difficulty track the cheap prior / raw size?
    print("\n=== sanity (not H5) ===")
    prior_rank = {"low": 0, "mid": 1, "high": 2}
    xs = [latent[i] for i in ids]
    print(f"  vs difficulty_prior: rho="
          f"{spearman(xs, [prior_rank.get(index[i].get('difficulty_prior'),1) for i in ids]):+.3f}")
    print(f"  vs churn(add+del):   rho="
          f"{spearman(xs, [ (index[i].get('additions') or 0)+(index[i].get('deletions') or 0) for i in ids]):+.3f}")
    return 0


def _composite(index: dict, ids: list[str]) -> list[float]:
    """Rank-sum review-effort proxy across reviewers/ttm/changes_req/comments."""
    keys = ["num_reviewers", "time_to_merge_hours", "changes_requested", "review_comments"]
    cols = {k: rankdata([float(index[i]["review_signals"].get(k) or 0) for i in ids])
            for k in keys}
    return [sum(cols[k][n] for k in keys) for n in range(len(ids))]


if __name__ == "__main__":
    raise SystemExit(main())
