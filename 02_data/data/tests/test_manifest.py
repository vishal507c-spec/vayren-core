"""Manifest — the declared identity of the historical_data component."""

from core.contracts.manifest import validate_manifest

from data.manifest import data_manifest


def test_manifest_validates() -> None:
    manifest = data_manifest()
    result = validate_manifest(manifest)
    assert result.valid, result.errors


def test_manifest_identity_and_version() -> None:
    manifest = data_manifest()
    assert manifest.identity.name == "historical_data"
    assert str(manifest.version) == "1.0.0"
    assert manifest.type == "ingest"


def test_manifest_declares_three_capabilities() -> None:
    ids = {cap.id.value for cap in data_manifest().capabilities}
    assert ids == {
        "historical_data.download",
        "historical_data.coverage",
        "historical_data.status",
    }


def test_manifest_depends_only_on_core() -> None:
    manifest = data_manifest()
    assert [dep.name for dep in manifest.dependencies] == ["core"]


def test_manifest_declares_events() -> None:
    manifest = data_manifest()
    assert set(manifest.events_consumed) == {
        "DownloadRequest",
        "CoverageRequest",
        "CancelDownload",
    }
    assert set(manifest.events_produced) == {
        "DownloadStarted",
        "DownloadProgress",
        "DownloadCompleted",
        "DownloadFailed",
        "DownloadCoverage",
    }


def test_manifest_contains_no_secrets() -> None:
    import json

    manifest = data_manifest()
    dump = json.dumps(manifest.__dict__, default=str)
    for secret_word in ("api_key", "api_secret", "password", "totp", "token"):
        assert secret_word not in dump.lower()
