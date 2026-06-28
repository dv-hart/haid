"""The forest model of a session: roots, the active branch, and rewind detection.

A transcript is NOT a line and not even a single tree — it is a forest (verified across
65 real transcripts; see plans/phase1-build.md §0.5). This module turns parsed records
into that structure and answers the questions the rest of HAID depends on:

  - Which records form the conversation tree (threaded, deduped by uuid)?
  - What is the ACTIVE branch the user ended on? = the latest `last-prompt.leafUuid`
    walked to its root — with a mandatory fallback (latest-timestamp main-thread leaf)
    because that pointer is absent/dangling in ~18% of real files.
  - Where are the REWINDS (abandoned branches)? = off-active-path genuine user prompts,
    in two on-disk shapes: a sibling fork (edit-and-resubmit) or an off-path chain.
  - What TIMELINES did the model actually experience? Each is one root→leaf path. The
    correctness rule downstream: scope every waste metric WITHIN a timeline, never across
    the flattened record set, or cross-branch repeats become phantom re-reads.

Structural forks (one assistant turn with parallel tool_use → multiple tool_result
children, or async-subagent attach) are NOT rewinds and are reported separately.

Stdlib only; no model.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from . import records as rec


@dataclass
class Rewind:
    """An abandoned branch: a real user prompt that is off the active path."""
    prompt_uuid: str
    prompt_text: str
    timestamp: str | None
    shape: str                 # "sibling-fork" | "off-path-chain"
    divergence_uuid: str | None  # nearest ancestor that IS on the active path


@dataclass
class Timeline:
    """One root→leaf path the model actually experienced (shared prefixes are repeated
    across timelines by design — consumers scope within a single timeline)."""
    label: str                 # "active" or "rewind:<short-uuid>"
    leaf_uuid: str
    node_uuids: list[str]      # root → leaf order
    is_active: bool


class Forest:
    def __init__(self, records: list[rec.Record]):
        # Dedup by uuid (resume can replay shared history across files); keep first seen.
        self.by_uuid: dict[str, rec.Record] = {}
        self.n_duplicate_uuids = 0
        self._last_prompt_leaf: str | None = None
        for r in records:
            if r.type == "last-prompt":
                lu = r.raw.get("leafUuid")
                if lu:
                    self._last_prompt_leaf = lu  # last one wins (file order)
            if not r.is_threaded():
                continue
            if r.uuid in self.by_uuid:
                self.n_duplicate_uuids += 1
                continue
            self.by_uuid[r.uuid] = r

        self.children: dict[str | None, list[str]] = defaultdict(list)
        for u, r in self.by_uuid.items():
            self.children[r.parent_uuid].append(u)

        self.roots: list[str] = [
            u for u, r in self.by_uuid.items()
            if r.parent_uuid is None or r.parent_uuid not in self.by_uuid
        ]
        self.active_leaf, self.active_leaf_method = self._resolve_active_leaf()
        self.active_path: list[str] = self._path_to_root(self.active_leaf)  # root→leaf
        self.active_set: set[str] = set(self.active_path)
        self.rewinds: list[Rewind] = self._detect_rewinds()

    # --- active branch ------------------------------------------------------------------

    def _leaves(self) -> list[str]:
        parents = set(r.parent_uuid for r in self.by_uuid.values())
        return [u for u in self.by_uuid if u not in parents]

    def _resolve_active_leaf(self) -> tuple[str | None, str]:
        lp = self._last_prompt_leaf
        if lp and lp in self.by_uuid:
            return lp, "leafUuid"
        # Fallback: latest-timestamp main-thread leaf (leafUuid dangling in ~18% of files).
        main_leaves = [u for u in self._leaves() if not self.by_uuid[u].is_sidechain]
        if not main_leaves:
            main_leaves = self._leaves()
        if not main_leaves:
            return None, "none"
        leaf = max(main_leaves, key=lambda u: self.by_uuid[u].timestamp or "")
        return leaf, "timestamp-fallback"

    def _path_to_root(self, leaf: str | None) -> list[str]:
        path: list[str] = []
        cur = leaf
        while cur and cur in self.by_uuid:
            path.append(cur)
            cur = self.by_uuid[cur].parent_uuid
        path.reverse()
        return path

    def _nearest_active_ancestor(self, uuid: str) -> str | None:
        cur = self.by_uuid[uuid].parent_uuid
        while cur and cur in self.by_uuid:
            if cur in self.active_set:
                return cur
            cur = self.by_uuid[cur].parent_uuid
        return None

    # --- rewinds ------------------------------------------------------------------------

    def _detect_rewinds(self) -> list[Rewind]:
        out: list[Rewind] = []
        for u, r in self.by_uuid.items():
            if u in self.active_set or not r.is_user_prompt():
                continue
            # Shape: sibling fork iff this prompt's parent also has a child on the active
            # path (the surviving twin); otherwise it diverged further up = off-path chain.
            sibs = self.children.get(r.parent_uuid, [])
            shape = "sibling-fork" if any(s in self.active_set for s in sibs) else "off-path-chain"
            out.append(Rewind(
                prompt_uuid=u,
                prompt_text=r.text().strip()[:120],
                timestamp=r.timestamp,
                shape=shape,
                divergence_uuid=self._nearest_active_ancestor(u),
            ))
        out.sort(key=lambda rw: rw.timestamp or "")
        return out

    # --- structural vs semantic forks ---------------------------------------------------

    def structural_forks(self) -> list[str]:
        """Parents with >1 child (multi-tool / subagent attach / rewind sibling). Reported
        for transparency; most are NOT rewinds."""
        return [p for p, cs in self.children.items() if p is not None and len(cs) > 1]

    # --- timelines ----------------------------------------------------------------------

    def _subtree_leaves(self, root: str) -> list[str]:
        seen, stack, leaves = set(), [root], []
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            kids = self.children.get(n, [])
            if not kids:
                leaves.append(n)
            stack.extend(kids)
        return leaves

    def timelines(self) -> list[Timeline]:
        """The active timeline plus one per rewind (root → that branch's latest leaf)."""
        out: list[Timeline] = []
        if self.active_leaf:
            out.append(Timeline("active", self.active_leaf, list(self.active_path), True))
        for rw in self.rewinds:
            leaves = self._subtree_leaves(rw.prompt_uuid)
            tip = max(leaves, key=lambda u: self.by_uuid[u].timestamp or "")
            out.append(Timeline(
                label=f"rewind:{rw.prompt_uuid[:8]}",
                leaf_uuid=tip,
                node_uuids=self._path_to_root(tip),
                is_active=False,
            ))
        return out

    # --- reporting ----------------------------------------------------------------------

    def summary(self) -> dict:
        return {
            "threaded_records": len(self.by_uuid),
            "duplicate_uuids": self.n_duplicate_uuids,
            "roots": len(self.roots),
            "active_leaf_method": self.active_leaf_method,
            "active_path_len": len(self.active_path),
            "rewinds": len(self.rewinds),
            "rewind_shapes": {
                s: sum(1 for rw in self.rewinds if rw.shape == s)
                for s in ("sibling-fork", "off-path-chain") if any(rw.shape == s for rw in self.rewinds)
            },
            "structural_forks": len(self.structural_forks()),
        }
