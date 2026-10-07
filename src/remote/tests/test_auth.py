"""Auth tests: separate remote credentials, scopes, attempt limits."""

import pytest

from remote.auth import (
    AuthConfigError,
    AuthTracker,
    RemoteRole,
    load_token_map,
    verify_token,
)

TRADE = "trade-secret-0123456789abcdef"
READ = "read-secret-0123456789abcdef"


def test_load_tokens_from_env() -> None:
    token_map = load_token_map({"VAYREN_REMOTE_TOKEN": TRADE, "VAYREN_REMOTE_READ_TOKEN": READ})
    assert token_map[TRADE].role == RemoteRole.TRADE
    assert token_map[READ].role == RemoteRole.READ


def test_missing_tokens_refuse_to_configure() -> None:
    with pytest.raises(AuthConfigError):
        load_token_map({})


def test_short_tokens_are_dropped_and_fail_closed() -> None:
    with pytest.raises(AuthConfigError):
        load_token_map({"VAYREN_REMOTE_TOKEN": "short"})


def test_blank_and_whitespace_tokens_ignored() -> None:
    with pytest.raises(AuthConfigError):
        load_token_map({"VAYREN_REMOTE_TOKEN": "   "})


def test_token_file_loading(tmp_path) -> None:  # noqa: ANN001
    token_file = tmp_path / "tokens.txt"
    token_file.write_text(
        "# display clients\nread:read-file-secret-0123456789\ntrade:trade-file-secret-0123456789\n",
        encoding="utf-8",
    )
    token_map = load_token_map({"VAYREN_REMOTE_TOKEN_FILE": str(token_file)})
    assert token_map["read-file-secret-0123456789"].role == RemoteRole.READ
    assert token_map["trade-file-secret-0123456789"].role == RemoteRole.TRADE


def test_token_file_bad_line_fails_closed(tmp_path) -> None:  # noqa: ANN001
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("bogus-line-without-role-or-length\n", encoding="utf-8")
    with pytest.raises(AuthConfigError):
        load_token_map({"VAYREN_REMOTE_TOKEN_FILE": str(token_file)})


def test_token_file_missing_fails_closed(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(AuthConfigError):
        load_token_map({"VAYREN_REMOTE_TOKEN_FILE": str(tmp_path / "nope.txt")})


def test_verify_token_match_miss_and_empty() -> None:
    token_map = load_token_map({"VAYREN_REMOTE_TOKEN": TRADE})
    assert verify_token(TRADE, token_map) is not None
    assert verify_token("wrong-secret-0123456789abcdef", token_map) is None
    assert verify_token("", token_map) is None


def test_roles_gate_commands() -> None:
    assert RemoteRole.allows_command(RemoteRole.TRADE) is True
    assert RemoteRole.allows_command(RemoteRole.READ) is False
    assert RemoteRole.allows_command("anything-else") is False


def test_tracker_success_and_attempt_cap() -> None:
    tracker = AuthTracker(max_attempts=3)
    assert tracker.authenticated is False
    assert tracker.note_attempt(None) is True
    assert tracker.note_attempt(None) is True
    assert tracker.note_attempt(None) is False
    assert tracker.attempts == 3


def test_tracker_records_role_on_success() -> None:
    token_map = load_token_map({"VAYREN_REMOTE_TOKEN": TRADE})
    tracker = AuthTracker()
    assert tracker.note_attempt(verify_token(TRADE, token_map)) is True
    assert tracker.authenticated_role == RemoteRole.TRADE
