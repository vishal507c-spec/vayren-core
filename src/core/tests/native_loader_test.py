"""Contract tests for core.native.loader (ABI version, library discovery, fail-closed handling)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.native.loader import (
    ABI_VERSION,
    ORDER_STATE_COUNT,
    NativeBridgeError,
    _candidate_names,
    find_library,
    load_vayren_core,
)


def test_abi_and_state_constants() -> None:
    assert ABI_VERSION == 1
    assert ORDER_STATE_COUNT == 13


def test_candidate_names_not_empty() -> None:
    names = _candidate_names()
    assert len(names) >= 1
    assert any("vayren_core" in n for n in names)


def test_blank_env_var_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAYREN_NATIVE_LIB", "   ")
    with pytest.raises(ValueError, match="whitespace only"):
        find_library()


def test_missing_env_var_file_raises_native_bridge_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    non_existent = tmp_path / "does_not_exist.dll"
    monkeypatch.setenv("VAYREN_NATIVE_LIB", str(non_existent))
    with pytest.raises(NativeBridgeError, match="missing file"):
        find_library()


def test_valid_env_var_finds_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_lib = tmp_path / "vayren_core.dll"
    fake_lib.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("VAYREN_NATIVE_LIB", str(fake_lib))
    found = find_library()
    assert found == fake_lib.resolve()


def test_load_vayren_core_abi_handshake() -> None:
    # If built, verify the actual cdylib handshake
    try:
        lib = load_vayren_core()
    except NativeBridgeError:
        pytest.skip("vayren_core native library not built on this host")
    assert lib.vy_abi_version() == ABI_VERSION
    assert lib.vy_order_state_count() == ORDER_STATE_COUNT
