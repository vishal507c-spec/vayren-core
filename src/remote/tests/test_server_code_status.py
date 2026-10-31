"""Regression tests for server code status verification."""

from __future__ import annotations

from unittest.mock import patch

from remote.server_code_status import (
    STATUS_NOT_UPDATED,
    STATUS_UPDATED,
    clear_cache_for_testing,
    evaluate_code_status,
    get_server_code_status,
    set_running_sha_for_testing,
)

SHA_A = "4cdf067585283271fcd4d900aa85752b300ac25f"
SHA_B = "9c886bd000000000000000000000000000000000"
SHA_C = "787ab98000000000000000000000000000000000"


def setup_function() -> None:
    clear_cache_for_testing()


def teardown_function() -> None:
    clear_cache_for_testing()


def test_evaluate_code_status_all_match_updated() -> None:
    """When GitHub origin/main, deployed files, and running process all match."""
    status, updated, detail = evaluate_code_status(
        github_sha=SHA_A, deployed_sha=SHA_A, running_sha=SHA_A
    )
    assert status == STATUS_UPDATED
    assert updated is True
    assert "in sync" in detail.lower()


def test_evaluate_code_status_deployed_matches_github_but_running_old_not_updated() -> None:
    """Explicit requirement test: EC2 files are latest (deployed == github),

    but running process is old (running != github) -> status MUST be NOT UPDATED.
    """
    status, updated, detail = evaluate_code_status(
        github_sha=SHA_A,
        deployed_sha=SHA_A,
        running_sha=SHA_B,  # Old process still in memory!
    )
    assert status == STATUS_NOT_UPDATED
    assert updated is False
    assert "Running process SHA" in detail


def test_evaluate_code_status_deployed_behind_github_not_updated() -> None:
    """When EC2 has not pulled latest commits (deployed != github)."""
    status, updated, detail = evaluate_code_status(
        github_sha=SHA_A,
        deployed_sha=SHA_B,
        running_sha=SHA_B,
    )
    assert status == STATUS_NOT_UPDATED
    assert updated is False
    assert "Deployed SHA" in detail


def test_evaluate_code_status_all_three_different_not_updated() -> None:
    """When GitHub, deployed disk, and running process all differ."""
    status, updated, detail = evaluate_code_status(
        github_sha=SHA_A,
        deployed_sha=SHA_B,
        running_sha=SHA_C,
    )
    assert status == STATUS_NOT_UPDATED
    assert updated is False


def test_evaluate_code_status_github_unreachable_empty_not_updated() -> None:
    """When GitHub is unreachable or fails to return SHA."""
    status, updated, detail = evaluate_code_status(
        github_sha="",
        deployed_sha=SHA_A,
        running_sha=SHA_A,
    )
    assert status == STATUS_NOT_UPDATED
    assert updated is False
    assert "unreachable" in detail.lower()


def test_evaluate_code_status_deployed_empty_not_updated() -> None:
    """When deployed working directory SHA cannot be read."""
    status, updated, detail = evaluate_code_status(
        github_sha=SHA_A,
        deployed_sha="",
        running_sha=SHA_A,
    )
    assert status == STATUS_NOT_UPDATED
    assert updated is False
    assert "unavailable" in detail.lower()


def test_evaluate_code_status_running_empty_not_updated() -> None:
    """When running process SHA is unavailable."""
    status, updated, detail = evaluate_code_status(
        github_sha=SHA_A,
        deployed_sha=SHA_A,
        running_sha="",
    )
    assert status == STATUS_NOT_UPDATED
    assert updated is False
    assert "unavailable" in detail.lower()


def test_evaluate_code_status_case_insensitive() -> None:
    """Git SHAs should compare case-insensitively."""
    status, updated, _ = evaluate_code_status(
        github_sha=SHA_A.upper(),
        deployed_sha=SHA_A.lower(),
        running_sha=SHA_A.lower(),
    )
    assert status == STATUS_UPDATED
    assert updated is True


def test_get_server_code_status_caching_and_force() -> None:
    """Verify that get_server_code_status caches results and force bypasses cache."""
    call_count = 0

    def mock_fetch(_repo_dir: object) -> str:
        nonlocal call_count
        call_count += 1
        return SHA_A

    with (
        patch("remote.server_code_status.fetch_github_origin_sha", side_effect=mock_fetch),
        patch("remote.server_code_status._read_head_commit_sha", return_value=SHA_A),
    ):
        set_running_sha_for_testing(SHA_A)

        # First call -> fetches
        res1 = get_server_code_status(force=False)
        assert res1["status"] == STATUS_UPDATED
        assert res1["updated"] is True
        assert call_count == 1

        # Second call without force -> uses cache (no new fetch)
        res2 = get_server_code_status(force=False)
        assert res2["status"] == STATUS_UPDATED
        assert call_count == 1

        # Third call with force=True -> bypasses cache and re-fetches
        res3 = get_server_code_status(force=True)
        assert res3["status"] == STATUS_UPDATED
        assert call_count == 2


def test_get_server_code_status_running_mismatch() -> None:
    """Integration test of get_server_code_status when running process is old."""
    with (
        patch("remote.server_code_status.fetch_github_origin_sha", return_value=SHA_A),
        patch("remote.server_code_status._read_head_commit_sha", return_value=SHA_A),
    ):
        set_running_sha_for_testing(SHA_B)  # Running is old!

        res = get_server_code_status(force=True)
        assert res["status"] == STATUS_NOT_UPDATED
        assert res["updated"] is False
        assert res["running_sha"] == SHA_B
        assert res["deployed_sha"] == SHA_A
        assert res["github_sha"] == SHA_A
