/* HAID visualizer — bus diagram, built piece by piece.
   CENTER COLUMN = a COLLAPSIBLE RECURSIVE TREE with the full grouping hierarchy:
     window ▸ episodes ▸ sessions ▸ exchanges ▸ (⊕ low-signal groups | file-op leaves)
   Every node carries the AGGREGATE of its descendants' file connections + token weight, so the
   buses we add later merge into a collapsed node (Factorio) and fan out when expanded.
   No spine line / dots (removed). Files / buses / margins still deferred. */

const SVGNS = "http://www.w3.org/2000/svg";
const el = (t, a = {}) => { const n = document.createElementNS(SVGNS, t); for (const k in a) n.setAttribute(k, a[k]); return n; };
const txt = (p, x, y, s, a = {}) => { const t = el("text", Object.assign({ x, y }, a)); t.textContent = s; p.appendChild(t); return t; };
const trim = (s, n) => (s && s.length > n ? s.slice(0, Math.max(0, n - 1)) + "…" : (s || ""));
const addTitle = (node, text) => { const t = el("title"); t.textContent = text; node.appendChild(t); };

const TOOL_COLOR = {
  Read: "#0969da", Edit: "#bc4c00", MultiEdit: "#bc4c00", Write: "#bc4c00",
  Bash: "#6e7781", Grep: "#8250df", Glob: "#8250df", Agent: "#1a7f37",
  TodoWrite: "#9a6700", WebFetch: "#0a7ea4", WebSearch: "#0a7ea4",
};
const toolColor = t => TOOL_COLOR[t] || "#6e7781";
const SIGNIFICANT = new Set(["Read", "Edit", "MultiEdit", "Write", "Agent"]);
const BASE_CAP = 9;   // colour lanes per side by default; "+N others" click expands to maxFit

let STATE = { theme: "light", exp: null };

// ---- columns ---------------------------------------------------------------------------
function columns(W) {
  const MARGIN = 58, FILES = 150;
  const flex = Math.max(420, W - (2 * MARGIN + 2 * FILES));
  const bus = flex * 0.15, steps = flex - 2 * bus;
  let x = 0;
  const col = w => { const c = { x0: x, w, x1: x + w, mid: x + w / 2 }; x += w; return c; };
  return {
    timeMargin: col(MARGIN), inputFiles: col(FILES), inputBus: col(bus),
    steps: col(steps), outputBus: col(bus), outputFiles: col(FILES), tokenMargin: col(MARGIN), total: x,
  };
}

// ---- aggregation (tracks per-file in/out tokens so merged connections size correctly) ---
const emptyAgg = () => ({ reads: 0, writes: 0, tokIn: 0, tokOut: 0, tok: 0, fileIn: {}, fileOut: {}, byTool: {} });
function addStep(a, s) {
  if (s.type !== "call") return a;
  a.byTool[s.tool] = (a.byTool[s.tool] || 0) + 1; a.tok += s.token_weight || 0;
  if (s.direction === "in" || s.direction === "both") { a.reads++; a.tokIn += s.token_weight || 0; if (s.file_id) a.fileIn[s.file_id] = (a.fileIn[s.file_id] || 0) + (s.token_weight || 0); }
  if (s.direction === "out" || s.direction === "both") { a.writes++; a.tokOut += s.token_weight || 0; if (s.file_id) a.fileOut[s.file_id] = (a.fileOut[s.file_id] || 0) + (s.token_weight || 0); }
  return a;
}
function combine(a, b) {
  a.reads += b.reads; a.writes += b.writes; a.tokIn += b.tokIn; a.tokOut += b.tokOut; a.tok += b.tok;
  for (const f in b.fileIn) a.fileIn[f] = (a.fileIn[f] || 0) + b.fileIn[f];
  for (const f in b.fileOut) a.fileOut[f] = (a.fileOut[f] || 0) + b.fileOut[f];
  for (const t in b.byTool) a.byTool[t] = (a.byTool[t] || 0) + b.byTool[t];
  return a;
}
function aggOf(node) {
  if (node._agg) return node._agg;
  if (node.kind === "leaf") return node._agg = addStep(emptyAgg(), node.step);
  const a = emptyAgg(); for (const c of node.children) combine(a, aggOf(c)); return node._agg = a;
}
const toolChips = byTool => Object.entries(byTool).map(([t, n]) => n > 1 ? `${t}×${n}` : t).join(" · ");

// ---- global file identity: color + lane rank, consistent across the whole window --------
function fileColor(i) { const h = (i * 137.508) % 360; return `hsl(${h.toFixed(0)} 62% 52%)`; }
function globalFiles(data) {
  if (data._gf) return data._gf;
  const by = {};
  for (const k in data.sessions) for (const it of data.sessions[k].spine) {
    if (it.kind !== "assistant") continue;
    for (const c of it.calls) {
      if (!c.file_id) continue;
      const f = by[c.file_id] || (by[c.file_id] = { id: c.file_id, name: (c.file || c.file_id).split("/").pop(), totIn: 0, totOut: 0 });
      if (c.direction === "in" || c.direction === "both") f.totIn += c.token_weight || 0;
      if (c.direction === "out" || c.direction === "both") f.totOut += c.token_weight || 0;
    }
  }
  const all = Object.values(by);
  all.slice().sort((a, b) => (b.totIn + b.totOut) - (a.totIn + a.totOut)).forEach((f, i) => f.color = fileColor(i));
  const inRank = all.filter(f => f.totIn > 0).sort((a, b) => b.totIn - a.totIn).map(f => f.id);
  const outRank = all.filter(f => f.totOut > 0).sort((a, b) => b.totOut - a.totOut).map(f => f.id);
  return data._gf = { by, inRank, outRank };
}

// ---- build the full window tree --------------------------------------------------------
function buildSteps(sess) {
  const out = [];
  for (const it of sess.spine) {
    if (it.kind === "user") out.push({ type: "user", text: it.text, ts: it.ts, cum: it.cum_ntok });
    else if (it.kind === "assistant") {
      if (it.has_text) out.push({ type: "say", text: it.text, ts: it.ts, cum: it.cum_ntok });
      for (const c of it.calls) out.push({ type: "call", ts: it.ts, cum: it.cum_ntok, ...c });
    }
  }
  return out;
}
function buildExchanges(sess, sid) {
  const steps = buildSteps(sess), exchanges = []; let cur = null, ei = 0;
  const open = user => { cur = { kind: "exchange", id: `${sid}e${ei++}`, user, raw: [], children: [] }; exchanges.push(cur); };
  for (const s of steps) { if (s.type === "user") open(s); else { if (!cur) open(null); cur.raw.push(s); } }
  for (const ex of exchanges) {
    let summary = null; const raw = ex.raw.slice();
    if (raw.length && raw[raw.length - 1].type === "say") summary = raw.pop();
    let run = [], ni = 0;
    const flush = () => { if (run.length) { ex.children.push({ kind: "group", id: `${ex.id}g${ni++}`, children: run.map((s, k) => ({ kind: "leaf", id: `${ex.id}g${ni}c${k}`, step: s })) }); run = []; } };
    for (const s of raw) { if (s.type === "call" && SIGNIFICANT.has(s.tool)) { flush(); ex.children.push({ kind: "leaf", id: `${ex.id}l${ni++}`, step: s }); } else run.push(s); }
    flush();
    if (summary) ex.children.push({ kind: "leaf", id: `${ex.id}s`, step: summary });
    ex.label = ex.user ? "❝ " + ex.user.text : "▸ session start";
  }
  return exchanges;
}
function epBadge(score) {
  // compact achievement badge on a scored episode node (score data from haid score)
  if (!score || score.achievement == null) return "";
  const bits = [`ach ${Math.round(score.achievement)}`];
  if (score.difficulty_rung != null) bits.push(`D${(+score.difficulty_rung).toFixed(0)}`);
  if (score.severe_count != null) bits.push(`${score.severe_count}sev`);
  return "  ◆ " + bits.join(" · ");
}
function buildWindow(data) {
  const win = { kind: "window", id: "win", label: data.window_label, children: [] };
  for (const ep of data.episodes) {
    const epNode = { kind: "episode", id: ep.id, label: ep.title + epBadge(ep.score), children: [] };
    for (const stem of ep.session_stems) {
      const sess = data.sessions[stem]; if (!sess) continue;
      epNode.children.push({
        kind: "session", id: "S" + stem, stem,
        label: `● ${stem} · ${sess.title || ""}`,
        children: buildExchanges(sess, "S" + stem),
      });
    }
    win.children.push(epNode);
  }
  return win;
}

// ---- flatten to visible rows -----------------------------------------------------------
function flatten(node, depth, rows) {
  rows.push({ node, depth });
  if (node.kind === "leaf") return rows;
  if (STATE.exp.has(node.id)) for (const c of node.children) flatten(c, depth + 1, rows);
  return rows;
}

const L = { padTop: 14, padBot: 30, rowH: 24, indent: 30 };

function render() {
  const data = window.HAID_DATA;
  document.body.dataset.theme = STATE.theme;
  const stage = document.getElementById("stage");
  stage.querySelectorAll("svg").forEach(s => s.remove());

  const W = Math.max(900, stage.clientWidth - 2);
  const C = columns(W);
  const win = buildWindow(data);
  const rows = flatten(win, 0, []);
  const N = rows.length;
  const rowY = i => L.padTop + i * L.rowH + L.rowH / 2;

  const zones = [[C.timeMargin, "TIME"], [C.inputFiles, "INPUT FILES"], [C.inputBus, "IN BUS"],
  [C.steps, "TURNS"], [C.outputBus, "OUT BUS"], [C.outputFiles, "OUTPUT FILES"], [C.tokenMargin, "TOKENS"]];
  const midX = C.steps.mid, baseW = C.steps.w - 20, charW = 6.5;

  // ---- per-row geometry ----
  const geo = rows.map((r, i) => {
    const cy = rowY(i), w = Math.max(220, baseW - r.depth * L.indent);
    return { ...r, i, cy, w, xL: midX - w / 2, xR: midX + w / 2 };
  });

  // ---- buses: lane allocation + right-angled taps, colour by file ----
  const gf = globalFiles(data);
  const busW = C.inputBus.w, maxFit = Math.max(6, Math.floor(busW / 7));
  const NCOLOR = Math.min(STATE.laneCap, maxFit);
  const FAN = 4, OVER = "#9aa0a6", COARSE_TAP_CAP = 3;

  // coarse = context (whole session/episode/window) → always ONE grey bundle, never eats a colour slot.
  // fine = focus (exchange/group/leaf) → competes for the 5 colour lanes.
  const COARSE = new Set(["session", "episode", "window"]);
  const emit = node => {
    if (node.kind === "leaf") {
      const s = node.step; if (s.type !== "call" || !s.file_id) return null;
      const e = { fine: true, inn: [], out: [] };
      if (s.direction === "in" || s.direction === "both") e.inn.push({ fid: s.file_id, tok: s.token_weight || 0 });
      if (s.direction === "out" || s.direction === "both") e.out.push({ fid: s.file_id, tok: s.token_weight || 0 });
      return e;
    }
    if (STATE.exp.has(node.id)) return null;                 // expanded → children carry it (merge undone)
    const a = aggOf(node), ents = m => Object.entries(m).map(([fid, tok]) => ({ fid, tok })).sort((x, y) => y.tok - x.tok);
    return { fine: !COARSE.has(node.kind), inn: ents(a.fileIn), out: ents(a.fileOut) };
  };

  // pass 1: rank only the FINE files per side → top NCOLOR get a colour lane; user-PINNED
  // overflow files get extra lanes after the ranked ones; everything else shares the grey
  // overflow lane (always the LAST slot, per side — sides can differ when pins differ).
  const rowEmits = geo.map(r => { const e = emit(r.node); return e ? { r, ...e } : null; });
  const sideTok = (sel, fineOnly) => { const m = {}; rowEmits.forEach(re => re && (re.fine || !fineOnly) && re[sel].forEach(c => { if (c.fid) m[c.fid] = (m[c.fid] || 0) + c.tok; })); return m; };
  const rankSlot = (m, pins) => {
    const slots = {};
    Object.entries(m).sort((a, b) => b[1] - a[1]).forEach(([f], i) => { if (i < NCOLOR) slots[f] = i; });
    const extra = pins.filter(f => slots[f] == null && m[f] != null);
    extra.forEach((f, i) => slots[f] = NCOLOR + i);
    const OV = NCOLOR + extra.length;
    for (const f in m) if (slots[f] == null) slots[f] = OV;
    return { slots, OV, K: OV + 1 };
  };
  // Rank fine (focus) files for the colour slots. When NOTHING fine is in view — the default
  // collapsed overview, every visible row a coarse session — fall back to ranking the coarse
  // aggregates so the window's top files still get colour lanes instead of one grey blob. The
  // fallback is gated on `coarseColour` below: as soon as anything fine is open (solo, exchanges),
  // fine wins the slots and coarse siblings stay grey — the original "don't let a collapsed sibling
  // outrank the focused session's leaf reads" guarantee is preserved.
  const anyFine = rowEmits.some(re => re && re.fine && (re.inn.length || re.out.length));
  const coarseColour = !anyFine;
  const inTokMap = sideTok("inn", anyFine), outTokMap = sideTok("out", anyFine);
  const SIDE = { in: rankSlot(inTokMap, [...STATE.pins.in]), out: rankSlot(outTokMap, [...STATE.pins.out]) };
  const slotInMap = SIDE.in.slots, slotOutMap = SIDE.out.slots;
  const spc = { in: busW / SIDE.in.K, out: busW / SIDE.out.K };
  // slot 0 (biggest file) sits nearest ITS file box → the thick feeder is SHORT; thinner taps run longer
  const xInLane = s => C.inputBus.x0 + (s + 0.5) * spc.in;
  const xOutLane = s => C.outputBus.x1 - (s + 0.5) * spc.out;
  const rankedOf = m => Object.entries(m).sort((a, b) => b[1] - a[1]);
  const inRanked = rankedOf(inTokMap), outRanked = rankedOf(outTokMap);
  const overflowTok = { in: {}, out: {} };   // every file routed to the grey lane (for the "+N others" box)

  // pass 2: per row, colour taps for top-5 files + ONE grey bundle tap for everything else
  const laneUse = {}, taps = [];
  const noteLane = (side, slot, x, color, y, name) => {
    const k = side + slot, u = laneUse[k] || (laneUse[k] = { x, color, minY: y, maxY: y, name, slot, side });
    u.minY = Math.min(u.minY, y); u.maxY = Math.max(u.maxY, y); u.x = x; u.color = color;
    if (name && slot !== SIDE[side].OV) u.name = name;
  };
  const bundleTitle = (files, prefix) => `${prefix}\n` + files.slice(0, 12).map(o => `  ${gf.by[o.fid].name} · ${fmtTok(o.tok)}t`).join("\n") + (files.length > 12 ? `\n  …+${files.length - 12} more` : "");
  rowEmits.forEach(re => {
    if (!re) return;
    const r = re.r;
    const doSide = (list, side, x0) => {
      const lxOf = slot => side === "in" ? xInLane(slot) : xOutLane(slot);
      const OV = SIDE[side].OV;
      // coarse context node → a single grey bundle for the whole thing, UNLESS this is the all-coarse
      // overview (coarseColour): then it splits like a fine row so the top files show as colour.
      if (!re.fine && !coarseColour) {
        const sum = list.reduce((s, e) => s + e.tok, 0), sorted = [...list].sort((a, b) => b.tok - a.tok);
        list.forEach(c => overflowTok[side][c.fid] = (overflowTok[side][c.fid] || 0) + c.tok);
        taps.push({ x0, lx: lxOf(OV), y: r.cy, color: OVER, tok: sum, lk: side + OV, side, count: list.length, title: bundleTitle(sorted, `${list.length} file${list.length > 1 ? "s" : ""} (${fmtTok(sum)}t):`), row: r.i, cyRow: r.cy });
        noteLane(side, OV, lxOf(OV), OVER, r.cy);
        return;
      }
      const slotMap = side === "in" ? slotInMap : slotOutMap;
      const colour = [], others = [];
      list.forEach(c => { const slot = slotMap[c.fid]; (slot != null && slot !== OV) ? colour.push({ ...c, slot }) : others.push(c); });
      colour.sort((a, b) => a.slot - b.slot);
      // A coarse overview row caps its colour taps at the top few (by global rank = lowest slot); the
      // rest fold into the grey bundle. Without this a 25-file session would stack ~10 fat teeth on one
      // 24px row. Fine rows (leaf/exchange) touch few files already → no cap.
      if (!re.fine && colour.length > COARSE_TAP_CAP) others.push(...colour.splice(COARSE_TAP_CAP));
      const items = colour.map(c => ({
        slot: c.slot, color: gf.by[c.fid].color, tok: c.tok, title: `${gf.by[c.fid].name} · ${fmtTok(c.tok)}t`, name: gf.by[c.fid].name,
      }));
      if (others.length) {
        others.sort((a, b) => b.tok - a.tok);
        others.forEach(o => overflowTok[side][o.fid] = (overflowTok[side][o.fid] || 0) + o.tok);
        const sum = others.reduce((s, e) => s + e.tok, 0);
        items.push({ slot: OV, color: OVER, tok: sum, count: others.length, title: bundleTitle(others, `+${others.length} more file${others.length > 1 ? "s" : ""}:`) });
      }
      const n = items.length;
      items.forEach((it, j) => {
        const y = r.cy + (j - (n - 1) / 2) * FAN;
        taps.push({ x0, lx: lxOf(it.slot), y, color: it.color, tok: it.tok, lk: side + it.slot, side, title: it.title, count: it.count || 0, row: r.i, cyRow: r.cy });
        noteLane(side, it.slot, lxOf(it.slot), it.color, y, it.name);
      });
    };
    if (re.inn.length) doSide(re.inn, "in", r.xL);
    if (re.out.length) doSide(re.out, "out", r.xR);
  });

  const inRankedC = inRanked.filter(([fid]) => slotInMap[fid] !== SIDE.in.OV);
  const outRankedC = outRanked.filter(([fid]) => slotOutMap[fid] !== SIDE.out.OV);

  // ===== thin STICKY caption strip (column headers only — files live in the wings, beside the rows) =====
  const headerH = 26;
  const hsvg = el("svg", { id: "hsvg", width: C.total, height: headerH, viewBox: `0 0 ${C.total} ${headerH}` });
  hsvg.appendChild(el("rect", { x: 0, y: 0, width: C.total, height: headerH, fill: "var(--bg)" }));
  zones.forEach(([z, label], i) => {
    if (i > 0) hsvg.appendChild(el("line", { class: "zone-sep", x1: z.x0, y1: 0, x2: z.x0, y2: headerH }));
    txt(hsvg, z.mid, 17, label, { class: "zone-cap", "text-anchor": "middle" });
  });

  // ---- file objects placed in the WINGS at their lane's first-use row (packed, no overlap) ----
  const boxH = 22, boxGap = 4;
  const fileLayout = side => {
    const slotMap = side === "in" ? slotInMap : slotOutMap, rankedC = side === "in" ? inRankedC : outRankedC;
    const items = rankedC.map(([fid, tok]) => {
      const slot = slotMap[fid], lane = laneUse[side + slot]; if (!lane) return null;
      const f = gf.by[fid], pinned = STATE.pins[side].has(fid);
      return { color: f.color, name: (pinned ? "★ " : "") + f.name, tok, lane, over: false, title: `${f.name} · ${fmtTok(tok)}t${pinned ? " · pinned — unpin from the +N others list" : ""}` };
    }).filter(Boolean);
    const over = overflowTok[side], ofids = Object.keys(over), olane = laneUse[side + SIDE[side].OV];
    if (ofids.length && olane) {
      const sum = ofids.reduce((s, f) => s + over[f], 0), listed = ofids.map(f => ({ name: gf.by[f].name, tok: over[f] })).sort((a, b) => b.tok - a.tok);
      items.push({ color: OVER, name: `+${ofids.length} other${ofids.length > 1 ? "s" : ""}`, tok: sum, lane: olane, over: true,
        title: `${ofids.length} file${ofids.length > 1 ? "s" : ""} (${fmtTok(sum)}t) — click to list them (pin any to its own lane)` });
    }
    items.sort((a, b) => a.lane.minY - b.lane.minY);   // pack downward from each lane's first tap
    let cursor = -Infinity;
    items.forEach(it => { it.y = Math.max((it.lane.firstTapY != null ? it.lane.firstTapY : it.lane.minY) - boxH / 2, cursor + boxGap); cursor = it.y + boxH; it.lane.top = Math.min(it.lane.minY, it.y + boxH / 2); });
    return items;
  };
  // ---- Sankey widths: bus total = GLOBAL FIXED log scale; each tap = its proportional share ----
  const byLane = {};
  taps.forEach(t => (byLane[t.lk] = byLane[t.lk] || []).push(t));
  const laneT = {}; for (const k in byLane) laneT[k] = byLane[k].reduce((s, t) => s + t.tok, 0);
  // same token count → same width in every view (cross-session comparable), log to span the range;
  // clamped to the lane spacing so lanes can't overlap. Anchored to the ACTUAL per-lane token range:
  // real sessions top a lane out around ~12-14k tok (per-file in+out), NOT the 200k context window.
  // Scale is biased toward the BIG lanes (the signal): REF_MAXW≈the lane-spacing cap so the curve
  // reaches full width only near the real max (~14k) instead of clamping at ~3k — the 3k..12k band
  // now fans out (3k≈7px, 5k≈8.5, 8k≈9.6, 12k≈cap) instead of all pinning to one fat width. The
  // price is the small end compresses to hairlines (sub-250 tok ≈ 1.2px), which is intended — those
  // reads are noise. 1k vs 3k still ≈2.7px apart.
  const REF_MINTOK = 250, REF_MAXTOK = 14000, REF_MAXW = 11;
  const lo = Math.log(REF_MINTOK), hi = Math.log(REF_MAXTOK), capW = Math.min(spc.in, spc.out) * 0.72;
  const busWidth = T => Math.min(capW, 1.2 + (REF_MAXW - 1.2) * Math.max(0, Math.min(1, (Math.log(Math.max(1, T)) - lo) / (hi - lo))));
  // Teeth must sum to EXACTLY W (flow conservation) or the trunk taper goes non-monotone.
  // Visibility floor for tiny taps is paid for by shrinking the larger taps (waterfall);
  // when the floor alone can't fit in W, drop it and split W evenly (hairline teeth).
  const MINW = 0.8;
  for (const k in byLane) {
    const ts = byLane[k], T = laneT[k], W = busWidth(T);
    laneUse[k].Wtotal = W;
    if (T <= 0 || ts.length * MINW >= W) { ts.forEach(t => t.w = W / ts.length); continue; }
    const floored = new Set(); let rem = W, remTok = T, changed = true;
    while (changed) {
      changed = false;
      for (const t of ts) {
        if (floored.has(t)) continue;
        if (rem * t.tok / remTok < MINW) { floored.add(t); t.w = MINW; rem -= MINW; remTok -= t.tok; changed = true; }
      }
    }
    ts.forEach(t => { if (!floored.has(t)) t.w = rem * t.tok / remTok; });
  }

  // ---- per-row tooth stacking: a row's taps stack by their real widths (replaces the fixed fan) ----
  const byRowSide = {};
  taps.forEach(t => (byRowSide[t.side + "·" + t.row] = byRowSide[t.side + "·" + t.row] || []).push(t));
  for (const k in byRowSide) {
    const ts = byRowSide[k];                       // emission order = slot order → no within-row crossings
    const gap = 1.6, H = ts.reduce((s, t) => s + t.w, 0) + gap * (ts.length - 1);
    let cur = ts[0].cyRow - H / 2;
    ts.forEach(t => { t.y = cur + t.w / 2; cur += t.w + gap; });
  }
  // same-lane teeth from adjacent rows: nudge down so tooth spans never overlap on the trunk
  for (const k in byLane) {
    const ts = byLane[k].sort((a, b) => a.y - b.y);
    for (let i = 1; i < ts.length; i++) {
      const minTop = ts[i - 1].y + ts[i - 1].w / 2 + 0.6;
      if (ts[i].y - ts[i].w / 2 < minTop) ts[i].y = minTop + ts[i].w / 2;
    }
    const u = laneUse[k];
    u.minY = Math.min(...ts.map(t => t.y - t.w / 2)); u.maxY = Math.max(...ts.map(t => t.y + t.w / 2));
    u.firstTapY = ts[0].y;
  }

  // ---- file boxes in the wings (packed at first use); each box is its lane's feeder mouth ----
  const wingItems = { in: fileLayout("in"), out: fileLayout("out") };
  ["in", "out"].forEach(side => wingItems[side].forEach(it => {
    const col = side === "in" ? C.inputFiles : C.outputFiles, bx = side === "in" ? col.x0 + 4 : col.x0 + 6, bw = col.w - 10;
    // snap the mouth onto the nearest tooth's centreline when their y-bands overlap: a feeder
    // meeting a tap at its own row must join it dead-on, or the comb outline degenerates into
    // a jogged width-step (mouth and tooth collinear but offset by half a tooth).
    let fy = it.y + boxH / 2, best = null;
    for (const t of byLane[it.lane.side + it.lane.slot] || [])
      if (Math.abs(t.y - fy) < (it.lane.Wtotal + t.w) / 2 + 0.5 && (!best || Math.abs(t.y - fy) < Math.abs(best.y - fy))) best = t;
    if (best) fy = best.y;
    it.lane.feed = { y: fy, tip: side === "in" ? bx + bw - 3 : bx + 3, w: it.lane.Wtotal };
  }));

  // ---- bus rendering: ONE continuous "comb" outline per lane (feeder + trunk + teeth).
  // The file-side edge of the trunk stays straight; each tap is a tooth peeling off the turns-side
  // edge, stepping the trunk inward by exactly the tooth's width (flow conserved by construction).
  // Corners get quarter-arc fillets; a bg "casing" pass under each comb makes crossings read as
  // over/under bridges instead of translucent mush.
  const roundedPath = (raw, R) => {
    const pts = raw.filter((p, i) => { const q = raw[(i + raw.length - 1) % raw.length]; return Math.abs(p[0] - q[0]) > 0.05 || Math.abs(p[1] - q[1]) > 0.05; });
    const n = pts.length; let d = "";
    // direction with a deadband: sub-quarter-px deltas are emission jitter, not a real diagonal.
    // Without it a long out-and-back with dy≈0.04 reads as diagonal and the fillet arcs land a
    // full r off-axis (the lens/carve-out corruption).
    const sgn = v => (Math.abs(v) < 0.25 ? 0 : Math.sign(v));
    for (let i = 0; i < n; i++) {
      const p = pts[i], a = pts[(i + n - 1) % n], b = pts[(i + 1) % n];
      const ix = sgn(p[0] - a[0]), iy = sgn(p[1] - a[1]);
      const ox = sgn(b[0] - p[0]), oy = sgn(b[1] - p[1]);
      const lin = Math.abs(p[0] - a[0]) + Math.abs(p[1] - a[1]), lout = Math.abs(b[0] - p[0]) + Math.abs(b[1] - p[1]);
      // p[2] = optional per-corner radius cap: a fillet is also bounded by the polygon's LOCAL
      // thickness (e.g. distance to the next tooth band), which the two adjacent segment lengths
      // can't see — without it a 5px tip arc slices across teeth sitting 2-4px apart.
      const r = Math.min(R, p[2] != null ? p[2] : R, lin / 2, lout / 2), cross = ix * oy - iy * ox;
      if (r < 0.5 || !cross) { d += (d ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); continue; }
      d += (d ? "L" : "M") + (p[0] - ix * r).toFixed(1) + " " + (p[1] - iy * r).toFixed(1);
      d += `A${r.toFixed(1)} ${r.toFixed(1)} 0 0 ${cross > 0 ? 1 : 0} ${(p[0] + ox * r).toFixed(1)} ${(p[1] + oy * r).toFixed(1)}`;
    }
    return d + "Z";
  };
  // thinnest DRAWN tooth: an even-split lane (169 taps over 5px) makes 0.03px teeth whose two tip
  // points collapse inside roundedPath's 0.05 dedup — the leftover retrace corrupted the outline.
  // Teeth are drawn at >= this height; the trunk still steps by the REAL width (flow conserved).
  const MIN_TOOTH = 0.3;
  const combPts = u => {
    const ts = byLane[u.side + u.slot];            // sorted by y above
    const s = u.side === "in" ? 1 : -1, W = u.Wtotal, xB = u.x - s * W / 2, X = w => xB + s * w;
    const J = ts.map(t => ({ y: t.y, w: t.w, we: Math.max(t.w, MIN_TOOTH), tip: t.x0 + s * 5, tooth: true }));
    if (u.feed) J.push({ y: u.feed.y, w: W, we: W, tip: u.feed.tip, tooth: false });
    J.sort((a, b) => a.y - b.y || (a.tooth ? 1 : -1));
    const fy = u.feed ? u.feed.y : -Infinity;
    let above = 0;                                 // flow width above each junction (conservation)
    // a tooth at EXACTLY the feeder's y (the snapped same-row tap) is fed by the mouth → counts
    // as below it (gets the full W); the feeder junction itself only sees the teeth above it.
    const wA = J.map(j => { const w = Math.abs(((j.tooth ? fy <= j.y : fy < j.y) ? W : 0) - above); if (j.tooth) above += j.w; return w; });
    const wB = J.map((j, i) => i + 1 < J.length ? wA[i + 1] : 0);
    const pts = [[X(0), J[0].y - J[0].we / 2]];    // top tip, on the straight base edge
    let mouth = null;
    J.forEach((j, i) => {
      if (j.tooth) pts.push([X(wA[i]), j.y - j.we / 2], [j.tip, j.y - j.we / 2], [j.tip, j.y + j.we / 2], [X(wB[i]), j.y + j.we / 2]);
      // feeder: step the far edge at the mouth's TOP edge — not inside the band (+wA), which cut
      // a notch into the band whenever teeth hang above the feeder (box displaced by packing).
      else { const ys = j.y - j.w / 2; pts.push([X(wA[i]), ys], [X(wB[i]), ys]); mouth = j; }
    });
    const bot = J[J.length - 1];
    pts.push([X(0), bot.y + bot.we / 2]);          // bottom tip, back on the base edge
    if (mouth) pts.push([X(0), mouth.y + mouth.w / 2], [mouth.tip, mouth.y + mouth.w / 2], [mouth.tip, mouth.y - mouth.w / 2], [X(0), mouth.y - mouth.w / 2]);
    return pts;
  };

  // ===== SCROLLING BODY — created now that content extents are known: wing-box stacks and lane
  // tails can run BELOW the last row, so the svg is sized to fit them, not just the rows =====
  const wingBot = ["in", "out"].flatMap(s => wingItems[s]).reduce((m, it) => Math.max(m, it.y + boxH), 0);
  const laneBot = Object.values(laneUse).reduce((m, u) => Math.max(m, u.maxY, u.feed ? u.feed.y + u.Wtotal / 2 : 0), 0);
  const bodyH = Math.max(L.padTop + N * L.rowH, wingBot, laneBot) + L.padBot;
  const bsvg = el("svg", { id: "bodySvg", width: C.total, height: bodyH, viewBox: `0 0 ${C.total} ${bodyH}` });
  const defs = el("defs"), clip = el("clipPath", { id: "stepClip" });
  clip.appendChild(el("rect", { x: C.steps.x0, y: 0, width: C.steps.w, height: bodyH })); defs.appendChild(clip); bsvg.appendChild(defs);
  zones.forEach(([z], i) => { if (i > 0) bsvg.appendChild(el("line", { class: "zone-sep", x1: z.x0, y1: 0, x2: z.x0, y2: bodyH })); });
  bsvg.appendChild(el("rect", { class: "turn-band", x: C.steps.x0, y: 0, width: C.steps.w, height: bodyH }));

  const gbus = el("g"); bsvg.appendChild(gbus);
  // draw far lanes first → the nearest/fattest lane bridges over crossings (casing makes the gap)
  Object.values(laneUse).filter(u => byLane[u.side + u.slot]).sort((a, b) => b.slot - a.slot).forEach(u => {
    const d = roundedPath(combPts(u), 5);
    gbus.appendChild(el("path", { class: "comb-casing", d }));
    const p = el("path", { class: "comb", d, fill: `color-mix(in srgb, ${u.color} 30%, var(--bg))`, stroke: u.color });
    addTitle(p, u.name || "other files"); gbus.appendChild(p);
    byLane[u.side + u.slot].forEach(t => {         // bundle teeth get a dashed centerline marker
      if (t.count && t.w >= 2.5) gbus.appendChild(el("path", { class: "tap-dash", d: `M${t.x0} ${t.y}H${u.x}` }));
    });
  });
  taps.forEach(t => {                              // invisible hit strokes carry the per-tap tooltips
    const hit = el("path", { class: "tap-hit", d: `M${t.x0} ${t.y}H${t.lx}`, "stroke-width": Math.max(10, t.w + 6) });
    addTitle(hit, t.title); gbus.appendChild(hit);
    if (t.count) txt(gbus, t.side === "in" ? t.lx - 4 : t.lx + 4, t.y + 3, "+" + t.count, { class: "bundle-lbl", "text-anchor": t.side === "in" ? "end" : "start" });
  });
  const drawBox = (side, it) => {
    const col = side === "in" ? C.inputFiles : C.outputFiles;
    const bx = side === "in" ? col.x0 + 4 : col.x0 + 6, bw = col.w - 10, cy = it.y + boxH / 2;
    const g = el("g", { class: "filebox" + (it.over ? " over" : ""), "data-overflow": it.over ? "1" : "0", "data-side": side, style: it.over ? "cursor:pointer" : "" });
    g.appendChild(el("rect", { x: bx, y: it.y, width: bw, height: boxH, rx: 4, fill: "var(--sq)", stroke: it.color, "stroke-width": 1.3 }));
    g.appendChild(el("rect", { x: side === "in" ? bx + bw - 3 : bx, y: it.y, width: 3, height: boxH, fill: it.color }));
    txt(g, bx + 8, cy + 3, trim((it.over ? "▸ " : "") + it.name, (bw - 42) / 5.6), { class: "fb-name" });
    txt(g, bx + bw - 6, cy + 3, fmtTok(it.tok) + "t", { class: "fb-tok", "text-anchor": "end" });
    addTitle(g, it.title); gbus.appendChild(g);
  };
  wingItems.in.forEach(it => drawBox("in", it)); wingItems.out.forEach(it => drawBox("out", it));

  // ---- overflow INSPECTOR PANEL (HTML overlay anchored to the grey box): the complete list of
  // bundled files. Clicking a row PINS that file to its own colour lane (slot after the ranked
  // NCOLOR) / unpins it; rows already holding a ranked lane are inert (their grey traffic is the
  // coarse-context share, which stays bundled by design).
  document.querySelectorAll(".ovpanel").forEach(p => p.remove());
  const buildPanel = side => {
    const box = wingItems[side].find(it => it.over);
    const tokMap = side === "in" ? inTokMap : outTokMap, slotMap = side === "in" ? slotInMap : slotOutMap;
    const over = overflowTok[side], OV = SIDE[side].OV;
    const fids = new Set([...Object.keys(over), ...STATE.pins[side]]);
    if (!box || !fids.size) return;
    const rows = [...fids].map(fid => {
      const pinned = STATE.pins[side].has(fid);
      const laned = slotMap[fid] != null && slotMap[fid] !== OV;
      return { fid, name: gf.by[fid].name, pinned, ranked: laned && !pinned,
               tok: (pinned ? tokMap[fid] : 0) || over[fid] || 0 };
    }).sort((a, b) => (b.pinned - a.pinned) || (b.tok - a.tok));
    const maxTok = Math.max(...rows.map(r => r.tok), 1);
    const div = document.createElement("div");
    div.className = "ovpanel";
    div.style.left = (side === "in" ? C.inputFiles.x0 + 4 : C.outputFiles.x1 - 6 - 290) + "px";
    div.style.top = (headerH + box.y + boxH + 6) + "px";
    div.innerHTML = `<div class="ovhead"><b>${rows.length}</b>&nbsp;bundled ${side === "in" ? "input" : "output"} files · click to pin<span class="ovclose">×</span></div>`;
    const list = document.createElement("div"); list.className = "ovlist"; div.appendChild(list);
    rows.forEach(r => {
      const e = document.createElement("div");
      e.className = "ovrow" + (r.pinned ? " pinned" : "") + (r.ranked ? " ranked" : "");
      e.title = r.ranked ? `${r.name} already has a lane — this is its collapsed-context share, which stays bundled`
        : (r.pinned ? "unpin — return to the grey bundle" : "pin to its own colour lane");
      e.innerHTML = `<span class="ovpin">${r.ranked ? "•" : r.pinned ? "★" : "☆"}</span>` +
        `<span class="ovsw" style="background:${r.pinned || r.ranked ? gf.by[r.fid].color : OVER}"></span>` +
        `<span class="ovname"></span><span class="ovtok">${fmtTok(r.tok)}t</span>` +
        `<span class="ovbar"><i style="width:${Math.max(2, 100 * r.tok / maxTok).toFixed(0)}%"></i></span>`;
      e.querySelector(".ovname").textContent = r.name;
      if (!r.ranked) e.onclick = () => { STATE.pins[side].has(r.fid) ? STATE.pins[side].delete(r.fid) : STATE.pins[side].add(r.fid); render(); };
      list.appendChild(e);
    });
    div.querySelector(".ovclose").onclick = () => { STATE.panel = null; render(); };
    stage.appendChild(div);
  };
  if (STATE.panel) buildPanel(STATE.panel);

  const body = el("g", { "clip-path": "url(#stepClip)" }); bsvg.appendChild(body);
  geo.forEach(r => {
    const cy = r.cy, node = r.node, w = r.w, x = r.xL;
    const sqH = L.rowH - 6, y = cy - sqH / 2;
    const expandable = node.kind !== "leaf";
    const open = STATE.exp.has(node.id);
    const g = el("g", {
      class: "row row-" + node.kind + (node.step && node.step.status === "error" ? " err" : ""),
      "data-id": node.id, "data-exp": expandable ? "1" : "0", style: expandable ? "cursor:pointer" : "",
    });

    if (node.kind === "leaf") {
      const s = node.step;
      g.appendChild(el("rect", { class: "sq", x, y, width: w, height: sqH, rx: 5 }));
      if (s.type === "say") {
        txt(g, x + 12, cy + 4, "▪ " + trim(s.text, (w - 20) / charW), { class: "sq-say" });
      } else {
        const tc = toolColor(s.tool);
        g.appendChild(el("rect", { x: x + 6, y: y + 4, width: 4, height: sqH - 8, rx: 2, fill: tc }));
        txt(g, x + 16, cy + 4, s.tool, { class: "sq-tool", fill: tc });
        const tw = s.token_weight ? fmtTok(s.token_weight) + "t" : "";
        const tgt = [s.file ? s.file.split("/").pop() : "", s.detail].filter(Boolean).join("  ");
        const reserve = (tw.length + 2) * charW;
        txt(g, x + 18 + tagW(s.tool), cy + 4, trim(tgt, (w - 24 - tagW(s.tool) - reserve) / charW), { class: "sq-label" });
        if (tw) txt(g, x + w - 10, cy + 4, tw, { class: "sq-tok", "text-anchor": "end" });
        if (s.derived) txt(g, x + w - 10 - (tw.length + 1) * charW, cy + 4, "~", { class: "sq-tok", "text-anchor": "end" });
      }
    } else {
      // container node: window / episode / session / exchange / group
      const cls = node.kind === "group" ? "sq sq-group" : "sq sq-" + node.kind;
      g.appendChild(el("rect", { class: cls, x, y, width: w, height: sqH, rx: node.kind === "window" ? 0 : 6 }));
      toggle(g, x + 12, cy, open);
      const a = aggOf(node);
      const right = badge(node, a);
      const rw = right ? right.length * charW + 12 : 0;
      let label = node.label;
      if (node.kind === "group") { const n = node.children.length; label = `⊕ ${n} small step${n > 1 ? "s" : ""} · ${toolChips(a.byTool) || "thinking / notes"}`; }
      txt(g, x + 25, cy + 4, trim(label, (w - 32 - rw) / charW), { class: "lbl lbl-" + node.kind });
      if (right) txt(g, x + w - 11, cy + 4, right, { class: "sq-badge", "text-anchor": "end" });
    }
    body.appendChild(g);
  });

  bsvg.onclick = e => {
    const ov = e.target.closest('[data-overflow="1"]');
    if (ov) { const s = ov.getAttribute("data-side"); STATE.panel = STATE.panel === s ? null : s; render(); return; }
    const row = e.target.closest("[data-id]");
    if (!row || row.getAttribute("data-exp") !== "1") return;
    const id = row.getAttribute("data-id");
    STATE.exp.has(id) ? STATE.exp.delete(id) : STATE.exp.add(id);
    render();
  };

  // temporary debug hook: ?debug=<file name substring> dumps that file's out-lane junctions
  window.__dbg = { byLane, laneUse, taps, spc };
  const dbgName = new URLSearchParams(location.search).get("debug");
  if (dbgName) {
    const pre = document.createElement("pre"); pre.id = "dbg";
    const out = [];
    Object.values(laneUse).forEach(u => {
      if (!(u.name || "").includes(dbgName)) return;
      const ts = byLane[u.side + u.slot] || [];
      out.push({ side: u.side, slot: u.slot, name: u.name, W: u.Wtotal, x: u.x, feed: u.feed,
        teeth: ts.map(t => ({ y: +t.y.toFixed(2), w: +t.w.toFixed(2), x0: t.x0, tok: Math.round(t.tok) })) });
    });
    pre.textContent = "DBG" + JSON.stringify(out, null, 1) + "GBD";
    document.body.appendChild(pre);
  }

  const footer = document.getElementById("legend");
  stage.insertBefore(hsvg, footer); stage.insertBefore(bsvg, footer);
  footer.innerHTML =
    `window ▸ ${data.episodes.length} episodes ▸ ${Object.keys(data.sessions).length} sessions ▸ exchanges ▸ steps · ` +
    `<b>${N}</b> rows · click ⊕ to drill · click <b>+N others</b> to list & pin files`;
}

function toggle(g, cx, cy, open) {
  g.appendChild(el("circle", { class: "tog", cx, cy, r: 6.5 }));
  txt(g, cx, cy + 3.2, open ? "−" : "+", { class: "tog-t", "text-anchor": "middle" });
}
// per-level right-side aggregate badge
function badge(node, a) {
  if (node.kind === "group") return a.tok ? fmtTok(a.tok) + "t" : "";
  const parts = [];
  if (node.kind === "window" || node.kind === "episode") parts.push(node.children.length + (node.kind === "window" ? " ep" : " sess"));
  if (a.reads) parts.push(a.reads + "R");
  if (a.writes) parts.push(a.writes + "W");
  parts.push(fmtTok(a.tok) + "t");
  return parts.join(" ");
}
const tagW = t => 12 + t.length * 6.4;
function fmtTok(n) { n = Math.round(n); if (n >= 1e6) return (n / 1e6).toFixed(1) + "M"; if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "k"; return "" + n; }

// ---- boot ----
function boot() {
  const data = window.HAID_DATA;
  const q = new URLSearchParams(location.search);
  STATE.exp = new Set();
  STATE.laneCap = parseInt(q.get("cap")) || BASE_CAP;
  STATE.pins = { in: new Set(), out: new Set() };
  STATE.panel = q.get("panel") || null;
  // headless-testing hooks: ?pinin=name.py,other.md / ?pinout=… pin files by display name
  const byName = {}; Object.values(globalFiles(data).by).forEach(f => byName[f.name] = f.id);
  ["in", "out"].forEach(side => (q.get("pin" + side) || "").split(",").filter(Boolean)
    .forEach(n => byName[n] && STATE.pins[side].add(byName[n])));
  if (q.get("theme")) STATE.theme = q.get("theme");
  initExpand(q.get("expand") || "overview");
  // testing hook: ?close=Sbccbf167,… removes node ids from the expand set after the preset
  (q.get("close") || "").split(",").filter(Boolean).forEach(id => STATE.exp.delete(id));
  document.querySelectorAll("[data-theme-btn]").forEach(b => b.onclick = () => {
    document.querySelectorAll("[data-theme-btn]").forEach(x => x.setAttribute("aria-pressed", "false"));
    b.setAttribute("aria-pressed", "true"); STATE.theme = b.dataset.themeBtn; render();
  });
  document.getElementById("expandAll").onclick = () => { initExpand("all"); render(); };
  document.getElementById("collapseAll").onclick = () => { STATE.exp = new Set(["win"]); render(); };
  document.getElementById("winLabel").textContent = data.window_label;
  render();
  window.addEventListener("resize", render);
}
// presets: "all" everything; "overview" window+episodes; "solo" first session fully; else just window
function initExpand(mode) {
  STATE.exp = new Set(["win"]);
  const data = window.HAID_DATA;
  if (mode.startsWith("solo")) {
    // window + one episode + one session + that session's whole subtree.
    // "solo" = first session; "solo:<stem>" picks a session (testing hook).
    const stem = mode.split(":")[1];
    const win = buildWindow(data); STATE.exp.add(win.id);
    let ep = win.children[0], sess = ep && ep.children[0];
    if (stem) for (const e of win.children) for (const s of e.children) if (s.stem === stem) { ep = e; sess = s; }
    if (!ep || !sess) return; STATE.exp.add(ep.id);
    const openAll = n => { if (n.kind === "leaf") return; STATE.exp.add(n.id); (n.children || []).forEach(openAll); };
    openAll(sess);
    return;
  }
  const OPEN = { overview: ["window", "episode"], sessions: ["window", "episode", "session"] };
  const walk = (node, d) => {
    if (node.kind === "leaf") return;
    const openIt = mode === "all" || (OPEN[mode] && OPEN[mode].includes(node.kind));
    if (openIt) STATE.exp.add(node.id);
    (node.children || []).forEach(c => walk(c, d + 1));
  };
  walk(buildWindow(data), 0);
}
window.addEventListener("DOMContentLoaded", boot);
