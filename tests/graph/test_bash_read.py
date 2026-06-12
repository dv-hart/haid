"""Bash-command read parser — high-precision, deterministic, stdlib only.

Locks the recognised single-file read forms (cat / sed -n / head / tail, plus one
trailing pager) AND the negative set the parser must refuse so read accounting never
invents a file or range: search/listing, remote, globs, redirection, in-place edits,
multi-file, command substitution. Run: PYTHONPATH=src python -m pytest tests/graph/ -q
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from haid.graph.bash_read import parse_bash_read


# --- positives: span derived from stdout / explicit range -------------------------------

def test_cat_whole_file_span_from_stdout():
    assert parse_bash_read("cat src/a.py", "l1\nl2\nl3\n") == ("src/a.py", (1, 4))


def test_cat_no_trailing_newline_counts_last_line():
    assert parse_bash_read("cat a.py", "x\ny") == ("a.py", (1, 3))


def test_cat_without_stdout_attributes_file_no_span():
    # File known, extent unknown -> None span (file-level fallback downstream).
    assert parse_bash_read("cat a.py") == ("a.py", None)


def test_sed_explicit_range_inclusive():
    # 'A,Bp' prints A..B inclusive -> half-open (A, B+1).
    assert parse_bash_read("sed -n '880,895p' logs/x.log") == ("logs/x.log", (880, 896))


def test_sed_single_line():
    assert parse_bash_read("sed -n '42p' a.py") == ("a.py", (42, 43))


def test_sed_range_clamped_to_short_stdout():
    # Asked for 100 lines, file only returned 3 -> end clamped.
    assert parse_bash_read("sed -n '1,100p' a.py", "a\nb\nc\n") == ("a.py", (1, 4))


def test_head_with_n_flag():
    assert parse_bash_read("head -n 100 a.py", "x\n" * 100) == ("a.py", (1, 101))


def test_cat_pipe_head_uses_piped_stdout():
    # The pager already truncated stdout; span reflects what actually came back.
    assert parse_bash_read("cat a.py | head -50", "x\n" * 50) == ("a.py", (1, 51))


def test_tail_plus_n_is_absolute():
    assert parse_bash_read("tail -n +200 a.py", "x\n" * 10) == ("a.py", (200, 210))


def test_tail_last_k_has_unknown_position():
    assert parse_bash_read("tail -n 20 a.py", "x\n" * 20) == ("a.py", None)


def test_cat_pipe_tail_unknown_position():
    assert parse_bash_read("cat a.py | tail -5", "x\n" * 5) == ("a.py", None)


# --- negatives: must NOT be treated as reads --------------------------------------------

def test_grep_is_search_not_read():
    assert parse_bash_read("grep -n foo a.py", "12:foo\n") is None


def test_cat_pipe_grep_is_search():
    assert parse_bash_read("cat a.py | grep foo", "foo\n") is None


def test_find_and_ls_rejected():
    assert parse_bash_read("find src -name '*.py'") is None
    assert parse_bash_read("ls -la src/") is None


def test_remote_ssh_rejected():
    # File lives on another host; its path would collide with local ids.
    assert parse_bash_read("ssh host 'cat /etc/hosts'") is None


def test_sed_in_place_is_a_write():
    assert parse_bash_read("sed -i 's/a/b/' a.py", "") is None


def test_sed_without_quiet_is_a_transform():
    assert parse_bash_read("sed '1,5p' a.py") is None


def test_glob_rejected():
    assert parse_bash_read("cat src/*.py") is None


def test_multiple_files_rejected():
    assert parse_bash_read("cat a.py b.py") is None


def test_redirection_rejected():
    assert parse_bash_read("cat a.py > out.txt") is None


def test_command_substitution_rejected():
    assert parse_bash_read("cat $(ls a.py)") is None


def test_chaining_rejected():
    assert parse_bash_read("cat a.py && echo done") is None


def test_stdin_dash_rejected():
    assert parse_bash_read("cat -") is None


def test_two_pagers_rejected():
    assert parse_bash_read("cat a.py | head | tail") is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
