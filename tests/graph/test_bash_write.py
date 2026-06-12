"""Bash-command write parser — high-precision, deterministic, stdlib only.

Locks the recognised single-file write forms (sed -i / redirection / tee / cp / mv)
AND the negatives it must refuse so modification tracking never invents a write:
search/read forms, /dev/null, heredocs, command substitution, remote, chaining,
globs, multi-file, and the real false-positive we observed (a `>` inside a quoted
sed script). Run: PYTHONPATH=src python -m pytest tests/graph/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.graph.bash_write import parse_bash_write, parse_heredoc_write


# --- positives --------------------------------------------------------------------------

def test_sed_in_place_edit():
    assert parse_bash_write("sed -i 's/a/b/' src/x.py") == ("src/x.py", "edit")


def test_sed_in_place_with_backup_suffix():
    assert parse_bash_write("sed -i.bak 's/a/b/' x.py") == ("x.py", "edit")


def test_sed_in_place_script_via_e_flag():
    assert parse_bash_write("sed -i -e 's/a/b/' x.py") == ("x.py", "edit")


def test_redirect_overwrite():
    assert parse_bash_write("python gen.py > out.json") == ("out.json", "overwrite")


def test_redirect_append():
    assert parse_bash_write("echo done >> log.txt") == ("log.txt", "append")


def test_tee_overwrite():
    assert parse_bash_write("cat in | tee dst.txt") == ("dst.txt", "overwrite")


def test_tee_append():
    assert parse_bash_write("cat in | tee -a dst.txt") == ("dst.txt", "append")


def test_cp_destination_is_the_write():
    assert parse_bash_write("cp config.example.yaml config.yaml") == ("config.yaml", "overwrite")


def test_mv_destination_is_the_write():
    assert parse_bash_write("mv old.py new.py") == ("new.py", "overwrite")


# --- negatives --------------------------------------------------------------------------

def test_sed_print_is_a_read_not_write():
    assert parse_bash_write("sed -n '1,5p' x.py") is None


def test_cat_is_not_a_write():
    assert parse_bash_write("cat x.py") is None


def test_redirect_to_devnull_ignored():
    assert parse_bash_write("pytest -q > /dev/null") is None


def test_stderr_dup_not_a_write():
    # `2>&1` is a tokenised unit, not a `>` redirection to a file.
    assert parse_bash_write("pytest -q 2>&1") is None


def test_quoted_gt_in_sed_script_is_not_a_redirect():
    # The observed real false positive: `>` lives inside the quoted script, not a redirect.
    assert parse_bash_write("cat .env | sed 's/=.*$/=<redacted>/'") is None


def test_heredoc_rejected():
    assert parse_bash_write("cat > /tmp/p.py << 'EOF'\nprint(1)\nEOF") is None


def test_command_substitution_rejected():
    assert parse_bash_write('git commit -m "$(cat msg)" > /tmp/x') is None


def test_remote_rejected():
    assert parse_bash_write("ssh host 'echo x > /etc/y'") is None


def test_chaining_rejected():
    assert parse_bash_write("cd src && sed -i 's/a/b/' x.py") is None


def test_glob_target_rejected():
    assert parse_bash_write("echo x > out*.txt") is None


def test_cp_to_directory_rejected():
    assert parse_bash_write("cp a.py src/") is None


def test_cp_multifile_rejected():
    assert parse_bash_write("cp a.py b.py src/") is None


def test_two_distinct_redirect_targets_rejected():
    assert parse_bash_write("cmd > a.txt > b.txt") is None


# --- heredoc (content-recovering, separate from parse_bash_write) ------------------------

def test_heredoc_overwrite_recovers_content():
    cmd = "cat > tests/t.py << 'EOF'\nimport x\nprint(1)\nEOF"
    assert parse_heredoc_write(cmd) == ("tests/t.py", "overwrite", "import x\nprint(1)\n")


def test_heredoc_append_recovers_content():
    cmd = "cat >> notes.md <<EOF\na line\nEOF"
    assert parse_heredoc_write(cmd) == ("notes.md", "append", "a line\n")


def test_heredoc_unquoted_with_expansion_yields_no_content():
    # An unquoted delimiter lets the shell expand $VAR/`cmd`; the literal body != what landed,
    # so we recognise the write (path/op) but refuse to trust the content.
    path, op, content = parse_heredoc_write("cat > a.sh <<EOF\nx=$HOME\nEOF")
    assert (path, op) == ("a.sh", "overwrite") and content is None


def test_heredoc_quoted_keeps_dollar_literal():
    _, _, content = parse_heredoc_write("cat > a.sh << 'EOF'\nx=$HOME\nEOF")
    assert content == "x=$HOME\n"


def test_heredoc_leading_cd_rejected():
    # A leading `cd OTHER && cat …` would resolve the target against the wrong dir — refuse.
    assert parse_heredoc_write("cd src && cat > a.py <<EOF\nx\nEOF") is None


def test_heredoc_unclosed_rejected():
    assert parse_heredoc_write("cat > a.py <<EOF\nstuff") is None


def test_plain_redirect_is_not_a_heredoc():
    assert parse_heredoc_write("echo x > a.py") is None


def test_parse_bash_write_still_rejects_heredoc():
    # parse_bash_write stays single-line/high-precision; heredocs go through parse_heredoc_write.
    assert parse_bash_write("cat > /tmp/p.py << 'EOF'\nprint(1)\nEOF") is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
