"""Comprehensive tests for the Universal Strategy Platform architecture."""

import tempfile
from pathlib import Path

from strategy.assistant import (
    analyze_strategy_code,
    generate_strategy_template,
)
from strategy.language.storage import (
    archive_strategy,
    create_strategy,
    list_strategy_records,
    rollback_strategy,
    update_strategy,
)
from strategy.registry import get_strategy_registry, reset_strategy_registry


def test_strategy_assistant_safety_diagnostics():
    # 1. Clean strategy code
    clean_code = generate_strategy_template("TrendFollower", indicator="EMA", timeframe="15m")
    diag = analyze_strategy_code(clean_code)
    assert diag.is_valid is True
    assert diag.syntax_error is None
    assert len(diag.security_violations) == 0
    assert "Fast Period" in [p["label"] for p in diag.parameters]

    # 2. Syntax error detection
    bad_syntax = "class BrokenStrategy(:\n    pass"
    diag_syntax = analyze_strategy_code(bad_syntax)
    assert diag_syntax.is_valid is False
    assert diag_syntax.syntax_error is not None

    # 3. Security violation detection (sandbox enforcement)
    malicious_code = """
import os
import subprocess
from strategy.strategies.base import PythonStrategy

class Exploit(PythonStrategy):
    def on_bar_logic(self, view):
        os.system("echo hacked")
"""
    diag_security = analyze_strategy_code(malicious_code)
    assert diag_security.is_valid is False
    assert any("os" in viol for viol in diag_security.security_violations)


def test_strategy_storage_versioning_and_rollback():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Create strategy
        initial_code = generate_strategy_template("AlphaOne")
        rec = create_strategy("AlphaOne", initial_code, version="1.0", data_dir=tmp_path)
        assert rec.name == "AlphaOne"
        assert rec.version == "1.0"
        assert rec.status == "ACTIVE"
        assert len(rec.versions) == 0

        # 2. Update with new code -> auto minor version bump & history snapshot
        updated_code = initial_code + "\n# updated logic"
        rec_updated = update_strategy(rec.id, new_code=updated_code, data_dir=tmp_path)
        assert rec_updated is not None
        assert rec_updated.version == "1.1"
        assert len(rec_updated.versions) == 1
        assert rec_updated.versions[0]["version"] == "1.0"
        assert rec_updated.versions[0]["code"] == initial_code

        # 3. Archive strategy
        archived = archive_strategy("AlphaOne", data_dir=tmp_path)
        assert archived is True
        rec_archived = list_strategy_records(tmp_path)[0]
        assert rec_archived.status == "ARCHIVED"

        # 4. Rollback to version 1.0
        rec_rolled_back = rollback_strategy("AlphaOne", "1.0", data_dir=tmp_path)
        assert rec_rolled_back is not None
        assert rec_rolled_back.code == initial_code
        assert rec_rolled_back.version == "1.2"


def test_central_strategy_registry_lifecycle():
    reset_strategy_registry()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        code = generate_strategy_template("MomentumScalp")
        create_strategy("MomentumScalp", code, data_dir=tmp_path)

        reg = get_strategy_registry(data_dir=tmp_path)
        assert reg.contains("obr-c1c4")
        assert reg.contains("MomentumScalp")

        # Test create_or_update via registry
        new_code = generate_strategy_template("DynamicVol")
        defn = reg.create_or_update(
            name="DynamicVol",
            code=new_code,
            timeframe="5m",
            direction="LONG",
            status="ACTIVE",
        )
        assert defn.name == "DynamicVol"
        assert reg.contains("DynamicVol")
        assert reg.get("DynamicVol").status == "ACTIVE"

        # Test archive via registry
        reg.archive("DynamicVol")
        assert reg.get("DynamicVol").status == "ARCHIVED"
