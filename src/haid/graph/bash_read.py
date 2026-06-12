"""Parse a Bash command into a single LOCAL file-read intent, when unambiguous.

Claude reads files through the shell as often as through the Read tool (`cat f`,
`sed -n '10,40p' f`, `head -n 100 f`). Those calls carry no `filePath` and no line
info in their result (a Bash `toolUseResult` is only `stdout`/`stderr`/flags), so
without this parser every shell read is invisible to the read-accounting metrics
(rereads, unused_context) and to the `reads` I/O edge — see docs/detectors.md.

This parser is deliberately HIGH-PRECISION, not greedy. On real sessions most
read-ish Bash is actually search/listing (`grep`/`find`/`ls`), runs on another host
(`ssh ...`), or is too tangled to attribute to one file+range (pipelines, globs,
command substitution). For all of those it returns ``None`` so downstream accounting
never invents a file or a line range. It recognises only the simple, unmistakable
forms:

    cat FILE                  -> (1, 1 + lines(stdout))
    head [-n K] FILE          -> (1, 1 + lines(stdout))
    sed -n 'A,Bp' FILE        -> (A, B + 1)        (clamped to stdout length)
    sed -n 'Ap' FILE          -> (A, A + 1)
    tail -n +N FILE           -> (N, N + lines(stdout))
    tail [-n K] FILE          -> span None          (absolute position unknown)
    <any of the above> | head|tail|cat              (one trailing pager only)

The span is half-open ``[start, end)``, 1-based start — the same convention
build.py uses for native Reads. A returned span of ``None`` means "this file was
read but the line range is unknown" (handled by the file-level fallback in
rereads). Stdlib only; no shelling out, no model.

Out of scope (returns None) and noted as known gaps: remote reads, `awk`, piped
transforms (`cat f | grep`), `sed` without `-n`, multi-file operands, and writes
(`sed -i`, `tee`, redirection) — the symmetric write gap is a separate pass.
"""

from __future__ import annotations

import re
import shlex

# Lead commands that are never a faithful single-file read.
_REMOTE = {"ssh", "docker", "kubectl", "podman", "scp", "rsync"}
_NOT_READ = {"grep", "rg", "egrep", "fgrep", "find", "ls", "wc", "diff",
             "awk", "tr", "sort", "uniq", "cut", "xxd", "od", "strings"}
_PAGERS = {"head", "tail", "cat"}

_GLOB = re.compile(r"[*?\[\]{}]")
_SED_RANGE = re.compile(r"^(\d+)(?:,(\d+))?p$")
_PLUS_N = re.compile(r"^\+(\d+)$")


def _lines(stdout: str | None) -> int:
    """Number of text lines in captured stdout (no trailing-newline off-by-one)."""
    if not stdout:
        return 0
    n = stdout.count("\n")
    if not stdout.endswith("\n"):
        n += 1
    return n


def _span_from_stdout(stdout: str | None) -> tuple[int, int] | None:
    """Whole-from-top read: lines 1..N, where N is what actually came back."""
    if stdout is None:
        return None
    return (1, 1 + _lines(stdout))


def _clamp(span: tuple[int, int], stdout: str | None) -> tuple[int, int]:
    """Shrink an explicit range to what stdout actually returned (a short file)."""
    if stdout is None:
        return span
    n = _lines(stdout)
    if n <= 0:
        return span
    s, e = span
    return (s, min(e, s + n))


def _single(files: list[str]) -> str | None:
    """Exactly one concrete (non-glob, non-stdin) file operand, else None."""
    if len(files) != 1:
        return None
    f = files[0]
    if not f or f == "-" or _GLOB.search(f):
        return None
    return f


def _files_simple(toks: list[str]) -> list[str]:
    """Operands for cat-like commands whose flags take no argument."""
    return [t for t in toks[1:] if not t.startswith("-")]


def _files_headtail(toks: list[str]) -> list[str]:
    """Operands for head/tail, where ``-n``/``-c`` consume the following token."""
    out: list[str] = []
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in ("-n", "-c"):
            i += 2
            continue
        if t.startswith("-"):          # -100, -n100, --lines=50
            i += 1
            continue
        out.append(t)
        i += 1
    return out


def _tail_span(toks: list[str], stdout: str | None) -> tuple[int, int] | None:
    """tail -n +N FILE starts at absolute line N; last-K tails have no known
    absolute position, so return None (the file is still attributed)."""
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in ("-n", "-c") and i + 1 < len(toks):
            m = _PLUS_N.match(toks[i + 1])
            if m and stdout is not None:
                start = int(m.group(1))
                return (start, start + _lines(stdout))
            i += 2
            continue
        i += 1
    return None


def _parse_sed(toks: list[str], stdout: str | None):
    flags = [t for t in toks[1:] if t.startswith("-")]
    # In-place edit is a WRITE, not a read.
    if any(f == "-i" or f.startswith("-i") for f in flags):
        return None
    # Require quiet mode: `sed -n 'A,Bp'` is the read form. Without -n, sed echoes
    # the whole file (and 'A,Bp' double-prints the range) — not a faithful read.
    has_n = any(f == "-n" or (not f.startswith("--") and "n" in f) for f in flags)
    if not has_n:
        return None
    script = None
    file = None
    for t in toks[1:]:
        if t.startswith("-"):
            continue
        m = _SED_RANGE.match(t)
        if m and script is None:
            script = m
        elif file is None:
            file = t
        else:
            return None                 # extra operand -> ambiguous
    if script is None or file is None:
        return None
    file = _single([file])
    if file is None:
        return None
    a = int(script.group(1))
    b = int(script.group(2)) if script.group(2) else a
    if b < a:
        return None
    return file, _clamp((a, b + 1), stdout)


def parse_bash_read(command: str, stdout: str | None = None):
    """Return ``(path, span_or_None)`` if ``command`` is an unambiguous single
    local file read, else ``None``. ``path`` is as written (build resolves it to a
    repo-relative id); ``span`` is half-open ``[start, end)``, 1-based, or ``None``
    when the line range can't be known. ``stdout`` (the captured result) resolves
    open-ended ranges and clamps over-long ones."""
    if not command:
        return None
    cmd = command.strip()
    # Non-local, write, or too-ambiguous to attribute -> bail before tokenizing.
    if "$(" in cmd or "`" in cmd:                       # command substitution
        return None
    if ";" in cmd or "&" in cmd or "||" in cmd:         # chaining / background
        return None
    if ">" in cmd or "<" in cmd:                        # redirection (write or odd read)
        return None

    parts = [p.strip() for p in cmd.split("|")]
    if len(parts) > 2:                                  # at most one trailing pager
        return None
    pager = None
    if len(parts) == 2:
        try:
            ptoks = shlex.split(parts[1])
        except ValueError:
            return None
        if not ptoks or ptoks[0] not in _PAGERS:        # `cat f | grep` is a search
            return None
        pager = ptoks[0]

    try:
        toks = shlex.split(parts[0])
    except ValueError:
        return None
    if not toks:
        return None
    lead = toks[0]
    if lead in _REMOTE or lead in _NOT_READ:
        return None

    if lead == "cat":
        f = _single(_files_simple(toks))
        if not f:
            return None
        span = None if pager == "tail" else _span_from_stdout(stdout)
        return f, span

    if lead == "head":
        if pager:                                       # head ... | pager: ambiguous
            return None
        f = _single(_files_headtail(toks))
        if not f:
            return None
        return f, _span_from_stdout(stdout)

    if lead == "tail":
        if pager:
            return None
        f = _single(_files_headtail(toks))
        if not f:
            return None
        return f, _tail_span(toks, stdout)

    if lead == "sed":
        if pager:
            return None
        return _parse_sed(toks, stdout)

    return None
