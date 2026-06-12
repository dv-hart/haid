# ADR-0002: Graph — in-memory build + SQLite persistence

**Status:** Accepted.

## Context

The session graph is the spine of the tool. A single session is small
(hundreds–thousands of nodes); cross-session analysis spans many sessions and the
audit is re-run repeatedly. We need rich traversal (backwards `why()`,
components for retry loops) and we don't want to re-parse unchanged JSONL every
run.

## Decision

**Hybrid:** persist a normalized store in **SQLite**; build the in-memory graph
(networkx, per [ADR-0001](0001-language-and-stack.md)) on demand from a scoped SQL
load; cache parsed-session artifacts keyed by file hash.

```
Layer 1  SQLite (source of truth)
   sessions, turns, toolcalls, files, regions, edges, episodes, commits
   + parsed_session(session_id, file_mtime, file_hash, blob)   # parse cache
Layer 2  Compute: load scoped rows → build networkx graph → run algorithms
   "one session"        → that session's rows
   "cross-session, file X" → edges touching X → induced subgraph
Layer 3  Cache: memoize derived artifacts (episodes, metrics)
   keyed by (session_id, file_hash, analysis_version)
```

Key edges table (the one that matters):

```sql
edges(id, type, src_id, dst_id, ts, weight REAL, confidence REAL, attrs JSON,
      session_id, episode_id,
      INDEX(src_id, type),       -- forward traversal
      INDEX(dst_id, type),       -- backward traversal (why/blame)
      INDEX(type, session_id));  -- scoped metric queries
```

**Incremental parsing:** transcripts are append-only within a session, so track a
byte offset and parse only appended lines for an active session; skip unchanged
sessions entirely (file-hash match). Bump `analysis_version` to invalidate caches
when detector logic changes.

## Rejected alternatives

- **Pure in-memory, re-parse every run** — fine for one session, wasteful across
  many; loses query-without-load-everything.
- **Dedicated graph DB (Neo4j / kuzu)** — buys nothing at this scale, adds an
  operational dependency; kuzu is archived. Reconsider only if cross-session grows
  to millions of nodes (not our regime).
- **DuckDB as graph engine** — awkward for traversal; only attractive if we later
  want columnar analytics over many sessions.

## Consequences

- Traversals run in-memory in networkx after a cheap SQL load — the relational
  store and the graph library coexist cleanly.
- One SQLite file per project keeps things portable and diffable.
- The parse cache is what makes repeated audits and the personal trend score cheap.
