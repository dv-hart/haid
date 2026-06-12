"""Parse a Bash command into a single LOCAL file-WRITE intent, when unambiguous.

The mirror of bash_read.py for the write side. Claude mutates files through the
shell with no Edit/Write block — `sed -i`, `cmd > file`, `>> file`, `| tee file`,
`cp`/`mv` — and those mutations are invisible to the graph: a re-read after a
`sed -i` looks like a *redundant* re-read (no edit seen), and a file written only
via Bash looks *unused*. This parser recovers the target file + op so the read
metrics stop firing falsely and the I/O edges are complete. See docs/detectors.md.

What it CANNOT recover is the written *content* (redirected stdout went to the file,
not to the captured result; a `sed -i` script isn't applied here; heredoc bodies are
opaque). So a Bash write participates in modification *tracking* (clearing the
re-read seen-ranges, giving unused-context credit, emitting an edits/produces edge)
but NOT in the content-based rework metric (`retouched`), which needs the actual
old/new lines. That limit is honest and documented, not papered over.

Returns ``(path, op)`` with ``op`` in {"edit", "overwrite", "append"}, else None.

High-precision, same conservatism as the read parser: it tokenizes with ``shlex``
(so a ``>`` inside a quoted `sed` script or string is NOT mistaken for a redirection
— a real false positive we observed) and REFUSES anything it can't attribute cleanly:
remote (`ssh`), command substitution, heredocs (`<<`, opaque body), command chaining
(`;`/`&&`/`||`, multi-target), globs, multi-file, and `/dev/*` targets. Stdlib only.

Out of scope for `parse_bash_write` (returns None), tracked as known gaps: chained writes
(`cd x && sed -i`), remote writes, and content recovery. Heredoc-to-file
(`cat > f << EOF`) is handled SEPARATELY by `parse_heredoc_write` below, because — unlike a
redirection — a heredoc carries its written *content* inline in the command, so it is the one
shell-write form whose content the reconstruction bridge CAN recover. `parse_bash_write` still
rejects heredocs (keeps it single-line/high-precision); the graph tries both.
"""

from __future__ import annotations

import re
import shlex

_REMOTE = {"ssh", "docker", "kubectl", "podman", "scp", "rsync"}
_REDIR_OPS = {">", ">>", "1>", "1>>"}
_GLOB = re.compile(r"[*?\[\]{}]")
_SED_INPLACE = re.compile(r"^(?:--in-place|-[a-zA-Z]*i)")   # -i, -i.bak, -ri, --in-place


def _opaque(cmd: str) -> bool:
    """True if the command can't be attributed to one local write target."""
    if "$(" in cmd or "`" in cmd:                       # command substitution
        return True
    if "<<" in cmd:                                     # heredoc — opaque body
        return True
    if ";" in cmd or "&&" in cmd or "||" in cmd:        # chaining — multi-target/ambiguous
        return True
    return False


def _single_file(files: list[str]) -> str | None:
    if len(files) != 1:
        return None
    f = files[0]
    if not f or f == "-" or f.startswith("/dev/") or f.endswith("/") or _GLOB.search(f):
        return None
    return f


def _sed_inplace_file(toks: list[str]) -> str | None:
    flags = [t for t in toks[1:] if t.startswith("-")]
    if not any(_SED_INPLACE.match(f) for f in flags):
        return None
    has_script_flag = any(t in ("-e", "-f") for t in toks)   # -e EXPR / -f FILE provide the script
    operands: list[str] = []
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in ("-e", "-f"):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        operands.append(t)
        i += 1
    # Need a file, plus an inline script unless one came from -e/-f.
    if not operands or (not has_script_flag and len(operands) < 2):
        return None
    return _single_file([operands[-1]])


def parse_bash_write(command: str):
    """Return ``(path, op)`` if ``command`` is an unambiguous single local file write,
    else ``None``. ``op`` is "edit" (in-place), "overwrite" (truncate+write), or
    "append". ``path`` is as written (build resolves it to a repo-relative id)."""
    if not command:
        return None
    cmd = command.strip()
    if _opaque(cmd):
        return None
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return None
    if not toks or toks[0] in _REMOTE:
        return None

    # 1) stdout redirection — only a STANDALONE operator token counts, so a `>` inside a
    #    quoted argument (e.g. sed 's/=.*$/=<redacted>/') is never mistaken for one.
    redirs = []
    for i, t in enumerate(toks):
        if t in _REDIR_OPS and i + 1 < len(toks):
            tgt = toks[i + 1]
            if tgt in _REDIR_OPS:
                continue
            if tgt.startswith("/dev/"):           # /dev/null etc. — not a tracked file
                continue
            if _GLOB.search(tgt) or tgt.endswith("/"):
                return None
            redirs.append((tgt, "append" if t.endswith(">>") else "overwrite"))
    if redirs:
        if len({f for f, _ in redirs}) != 1:      # >1 distinct target -> ambiguous
            return None
        return redirs[0]

    # 2) sed -i  (in-place edit; no redirection involved)
    if toks[0] == "sed":
        f = _sed_inplace_file(toks)
        return (f, "edit") if f else None

    # 3) ... | tee [-a] FILE
    if "tee" in toks:
        j = toks.index("tee")
        rest = toks[j + 1:]
        append = any(t in ("-a", "--append") for t in rest)
        f = _single_file([t for t in rest if not t.startswith("-")])
        return (f, "append" if append else "overwrite") if f else None

    # 4) cp / mv SRC DST  (the destination is the write)
    if toks[0] in ("cp", "mv"):
        operands = [t for t in toks[1:] if not t.startswith("-")]
        if len(operands) != 2:
            return None
        dst = _single_file([operands[1]])
        return (dst, "overwrite") if dst else None

    return None


# Header of a `cat >|>> TARGET <<[-] [q]DELIM[q]` heredoc, anchored at the command start so a
# leading `cd x && cat …` (different target dir) is conservatively NOT matched.
_HEREDOC_HDR = re.compile(
    r"""\s*cat\s+(>>?)\s*               # 1: op  (> overwrite | >> append)
        ("[^"]+"|'[^']+'|[^\s<>]+)      # 2: target (optionally quoted)
        \s*<<(-?)\s*                    # 3: '-' = strip leading tabs from body
        (['"]?)(\w+)\4                  # 4: opening quote (5: delimiter word)
    """,
    re.VERBOSE,
)


def parse_heredoc_write(command: str):
    """Recover a heredoc file write — ``(path, op, content)`` — or ``None``.

    Handles ``cat > FILE <<['"]?DELIM['"]? … DELIM`` and the ``>>`` (append) form. Unlike a
    plain redirection, the body IS in the command, so we return the written ``content`` too
    (the reconstruction bridge uses it; the graph ignores it). ``op`` is "overwrite" or
    "append". ``content`` is ``None`` when it can't be trusted — an UNQUOTED delimiter whose
    body contains ``$``/`` ` `` (the shell would expand it, so the literal body ≠ what landed).
    A ``None`` content still marks the write (so it's tracked + flagged), just not replayed.

    Deliberately conservative: must start with ``cat`` (no leading ``cd …&&``), single clean
    target, exactly one heredoc. Stdlib only.
    """
    if not command or "<<" not in command:
        return None
    m = _HEREDOC_HDR.match(command)
    if not m:
        return None
    op = "append" if m.group(1) == ">>" else "overwrite"
    target = m.group(2).strip("\"'")
    strip_tabs = m.group(3) == "-"
    quoted = bool(m.group(4))
    delim = m.group(5)
    if not target or target.startswith("/dev/") or target.endswith("/") or _GLOB.search(target):
        return None

    lines = command.split("\n")
    # The header line is the one carrying `<<DELIM`; the body runs until a line == delim.
    hdr_idx = next((i for i, ln in enumerate(lines) if "<<" in ln and _HEREDOC_HDR.match(ln)), None)
    if hdr_idx is None:
        return None
    body, closed = [], False
    for ln in lines[hdr_idx + 1:]:
        if ln.strip() == delim:
            closed = True
            break
        body.append(ln.lstrip("\t") if strip_tabs else ln)
    if not closed:
        return None
    content = "\n".join(body)
    if content and not content.endswith("\n"):
        content += "\n"
    if not quoted and re.search(r"[$`]", content):   # shell would expand → don't trust the body
        content = None
    return (target, op, content)
