"""Strategy Lab source-action + lifecycle tests.

Covers the regression class that wiped OBR and broke + NEW:
  * create/list/save/reopen/duplicate round-trips,
  * + NEW can never overwrite an existing strategy (OBR protection),
  * Copy All transfers the COMPLETE source (not a selection),
  * Format/Indent is token-safe (strings/comments/code verbatim),
  * OBR integrity: restored source is non-empty, compiles and hashes
    identically to its preserved version-graph snapshot.
"""

import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from strategy.language import compile_strategy  # noqa: E402
from strategy.language.compiler import StrategyLanguageError  # noqa: E402
from strategy.language.storage import (  # noqa: E402
    DEFAULT_CODE,
    LEGACY_OBR_NAME,
    create_strategy,
    delete_strategy,
    duplicate_strategy,
    list_strategies,
    load_strategy,
    load_strategy_record,
    save_strategy,
)  # noqa: E402

# A minimal but complete strategy — compiles and exposes the entry points.
_GOOD_SOURCE = (
    "from strategy.strategies.base import PythonStrategy\n"
    "\n"
    "\n"
    "class Strategy(PythonStrategy):\n"
    "    def on_bar_logic(self, view):\n"
    "        if view.bar.close > view.bar.open:\n"
    "            self.buy()\n"
)

# The production OBR library lives on the workstation that owns this repo;
# the integrity assertions run only when it is present.
OBR_LIBRARY = r"D:\VAYREN_STRATEGIES"
OBR_ID = "cf7d7645-4886-4b7f-9c6e-39f4cacc842f"
OBR_RESTORED_VERSION = "d258c55b-0ad9-49ee-8eaa-fd44b7e86adc"


# ── lifecycle: create / save / reopen / list / duplicate / delete ─────────


def test_create_strategy_appears_in_list(tmp_path):
    rec = create_strategy("Alpha", DEFAULT_CODE, tmp_path)
    assert rec.name == "Alpha"
    assert "Alpha" in list_strategies(tmp_path)
    # The created source reopens byte-identically.
    assert load_strategy("Alpha", tmp_path) == DEFAULT_CODE


def test_create_never_overwrites_existing_strategy(tmp_path):
    """+ NEW / create must refuse to clobber OBR or any saved strategy."""
    create_strategy("OBR", "class Strategy: pass", tmp_path)
    with pytest.raises(FileExistsError):
        create_strategy("OBR", "WIPED", tmp_path)
    # Untouched.
    assert load_strategy("OBR", tmp_path) == "class Strategy: pass"


def test_save_reopen_round_trip_preserves_identity(tmp_path):
    source = _GOOD_SOURCE
    save_strategy(source, "Beta", tmp_path)
    rec = load_strategy_record("Beta", tmp_path)
    assert rec is not None
    first_id = rec.id
    assert rec.code == source
    # A later save keeps the same record id (no identity loss).
    save_strategy(source + "\n", "Beta", tmp_path)
    assert load_strategy_record("Beta", tmp_path).id == first_id
    assert load_strategy("Beta", tmp_path) == source + "\n"


def test_duplicate_is_independent_copy(tmp_path):
    create_strategy("Gamma", "x = 1\n", tmp_path)
    duplicate_strategy("Gamma", "Gamma Copy", tmp_path)
    assert "Gamma Copy" in list_strategies(tmp_path)
    save_strategy("x = 2\n", "Gamma", tmp_path)
    # Duplicate keeps the original content.
    assert load_strategy("Gamma Copy", tmp_path) == "x = 1\n"


def test_delete_removes_and_leaves_others(tmp_path):
    create_strategy("Delta", "a\n", tmp_path)
    create_strategy("Epsilon", "b\n", tmp_path)
    assert delete_strategy("Delta", tmp_path) is True
    assert "Delta" not in list_strategies(tmp_path)
    assert "Epsilon" in list_strategies(tmp_path)


# ── editor: complete-source copy + token-safe format ─────────────────────


def _qt_app():
    from PySide6.QtWidgets import QApplication

    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        return inst
    return QApplication([])


def test_copy_all_copies_complete_source_not_selection():
    from PySide6.QtGui import QTextCursor

    from strategy.ui.code_editor import CodeEditor

    _qt_app()
    editor = CodeEditor()
    big = "line_a = 1\nline_b = 2\n" * 500
    editor.setPlainText(big)
    # Selection deliberately covers only part of the document.
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(10, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    assert editor.textCursor().selectedText() != big
    copied = editor.copy_all()
    assert copied == big  # complete source, regardless of selection


def test_reindent_preserves_strings_and_comments_verbatim():
    from strategy.ui.code_editor import _reindent_python

    src = 'def f():\n    s = "    spaced    "\n    # comment with    tabs\n    return s\n'
    out = _reindent_python(src)
    # String literal and comment content are untouched; only leading ws changes.
    assert '"    spaced    "' in out
    assert "# comment with    tabs" in out
    assert out == src  # already well-formed: idempotent no-op


def test_reindent_fixes_bad_indentation_without_losing_code():
    from strategy.ui.code_editor import _reindent_python

    # x=1 and if x: at the same (bad) indent; return nested deeper.
    src = "def f():\n  x = 1\n  if x:\n     return x\n"
    out = _reindent_python(src)
    assert out == "def f():\n    x = 1\n    if x:\n        return x\n"


def test_reindent_leaves_broken_source_untouched():
    from strategy.ui.code_editor import _reindent_python

    src = "def f(:\n"
    assert _reindent_python(src) == src


# ── compile: validation pipeline entry point ─────────────────────────────


def test_compile_reports_line_for_syntax_error():
    with pytest.raises(StrategyLanguageError) as exc_info:
        compile_strategy("class Strategy:\n    def f(:\n        pass\n")
    msg = str(exc_info.value)
    assert "line" in msg.lower() or "syntax" in msg.lower()


# ── OBR integrity: restored source matches its preserved snapshot ────────


def _obr_available() -> bool:
    import os.path

    return os.path.isdir(OBR_LIBRARY) and load_strategy_record("OBR", OBR_LIBRARY) is not None


@pytest.mark.skipif(not _obr_available(), reason="OBR library not present on this machine")
def test_obr_source_is_restored_and_intact():
    rec = load_strategy_record("OBR", OBR_LIBRARY)
    assert rec is not None
    assert rec.id == OBR_ID
    assert rec.code.strip(), "OBR source was wiped to empty — restore required"
    compiled = compile_strategy(rec.code)
    assert compiled.strategy_class is not None
    assert compiled.param_defaults, "OBR must expose its real parameters"


@pytest.mark.skipif(not _obr_available(), reason="OBR library not present on this machine")
def test_obr_matches_preserved_version_snapshot():
    from strategy.version import load_version

    version = load_version(OBR_ID, OBR_RESTORED_VERSION, OBR_LIBRARY)
    rec = load_strategy_record("OBR", OBR_LIBRARY)
    assert rec is not None
    current = "\n".join(line.rstrip() for line in rec.code.strip().splitlines())
    assert hashlib.sha256(current.encode("utf-8")).hexdigest() == version.source_hash


@pytest.mark.skipif(not _obr_available(), reason="OBR library not present on this machine")
def test_obr_present_alongside_builtin_seed_names():
    names = list_strategies(OBR_LIBRARY)
    assert "OBR" in names
    # The legacy seeded name still resolves when present.
    if LEGACY_OBR_NAME in names:
        assert load_strategy(LEGACY_OBR_NAME, OBR_LIBRARY) is not None


# ── isolation: one broken strategy must not break others ─────────────────


def test_broken_strategy_does_not_break_others(tmp_path):
    good = _GOOD_SOURCE
    create_strategy("GoodOne", good, tmp_path)
    create_strategy("BrokenOne", "this is not python(\n", tmp_path)

    # The good strategy still compiles while the broken one fails — isolation.
    assert compile_strategy(load_strategy("GoodOne", tmp_path)).strategy_class is not None
    with pytest.raises(StrategyLanguageError):
        compile_strategy(load_strategy("BrokenOne", tmp_path))

    # Library operations for the good strategy are unaffected.
    assert "GoodOne" in list_strategies(tmp_path)
    duplicate_strategy("GoodOne", "GoodOne Copy", tmp_path)
    assert "GoodOne Copy" in list_strategies(tmp_path)
