"""Stage 5 governance tests for snapshot testing framework.

Validates guard rails, security checks, and governance policies:
- Max snapshot size enforcement
- Secret/credential detection
- Snapshot count warnings
- Pre-commit workflow compliance
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from paths import get_snapshot_root
from src.utils.snapshot_governance import (
    check_file_size,
    scan_for_secrets,
    check_snapshot_security,
    check_snapshot_directory,
    MAX_SNAPSHOT_SIZE_MB,
)


def test_check_file_size_under_limit(tmp_path):
    """Test that small files pass size check."""
    test_file = tmp_path / "small.csv"
    test_file.write_text("timestamp,value\n1,2\n")

    result = check_file_size(test_file, max_size_mb=1.0)

    assert result["pass"] is True
    assert result["size_mb"] < 1.0


def test_check_file_size_over_limit(tmp_path):
    """Test that large files fail size check."""
    test_file = tmp_path / "large.csv"
    # Create ~1.1MB file
    large_content = "timestamp,value\n" + ("1,2.123456789\n" * 100000)
    test_file.write_text(large_content)

    result = check_file_size(test_file, max_size_mb=1.0)

    assert result["pass"] is False
    assert result["size_mb"] > 1.0
    assert "exceeds limit" in result["message"]


def test_scan_for_secrets_api_key():
    """Test detection of API keys in content."""
    # Build fake key to avoid GitHub push protection
    fake_key = "sk_" + "test_" + "abcdef1234567890abcdef1234567890"
    content = f"""
    config = {{
        "api_key": "{fake_key}"
    }}
    """

    findings = scan_for_secrets(content)

    assert len(findings) > 0
    assert any("api" in f["pattern"].lower() for f in findings)


def test_scan_for_secrets_password():
    """Test detection of passwords in content."""
    content = 'password = "MySecretP@ssw0rd123"'

    findings = scan_for_secrets(content)

    assert len(findings) > 0
    assert any("password" in f["pattern"].lower() for f in findings)


def test_scan_for_secrets_aws_credentials():
    """Test detection of AWS credentials."""
    # Build fake AWS keys to avoid GitHub push protection
    fake_access = "AKIA" + "IOSFODNN7EXAMPLE"
    fake_secret = "wJalr" + "XUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    content = f"""
    aws_access_key_id = {fake_access}
    aws_secret_access_key = {fake_secret}
    """

    findings = scan_for_secrets(content)

    assert len(findings) >= 2
    assert any("aws" in f["pattern"].lower() for f in findings)


def test_scan_for_secrets_clean_content():
    """Test that clean content produces no findings."""
    content = """
    timestamp,ticker,entry_signal,exit_flag
    2025-11-07 09:35:00,AAPL,0,0
    2025-11-07 09:40:00,AAPL,1,0
    """

    findings = scan_for_secrets(content)

    assert len(findings) == 0


def test_check_snapshot_security_clean_file(tmp_path):
    """Test security check on clean snapshot file."""
    snapshot = tmp_path / "signals__test.snapshot.csv"
    snapshot.write_text("timestamp,entry_signal\n1,0\n2,1\n")

    result = check_snapshot_security(snapshot)

    assert result["pass"] is True
    assert len(result["findings"]) == 0


def test_check_snapshot_security_with_secret(tmp_path):
    """Test security check detects secrets in metadata."""
    metadata = tmp_path / "test.metadata.json"
    # Construct fake secret to avoid triggering GitHub push protection
    fake_prefix = "sk_" + "live_"
    fake_secret = fake_prefix + "1234567890abcdefghijklmnop"
    bad_content = json.dumps({
        "name": "test",
        "api_key": fake_secret,
        "created_at": "2025-11-07"
    })
    metadata.write_text(bad_content)

    result = check_snapshot_security(metadata)

    assert result["pass"] is False
    assert len(result["findings"]) > 0


def test_check_snapshot_directory_empty(tmp_path):
    """Test governance check on empty directory."""
    result = check_snapshot_directory(tmp_path)

    assert result["pass"] is True
    assert result["count"] == 0
    assert len(result["oversized"]) == 0
    assert len(result["insecure"]) == 0


def test_check_snapshot_directory_normal(tmp_path):
    """Test governance check on normal snapshot directory."""
    # Create a few small, clean snapshots
    for i in range(3):
        snapshot = tmp_path / f"signals__test{i}.snapshot.csv"
        snapshot.write_text(f"timestamp,entry_signal\n{i},0\n")

        metadata = tmp_path / f"signals__test{i}.metadata.json"
        metadata.write_text(json.dumps({"name": f"test{i}", "rows": 1}))

    result = check_snapshot_directory(tmp_path, max_count=10)

    assert result["pass"] is True
    assert result["count"] == 3
    assert len(result["oversized"]) == 0
    assert len(result["insecure"]) == 0


def test_check_snapshot_directory_too_many(tmp_path):
    """Test governance check warns on too many snapshots."""
    # Create many snapshots
    for i in range(15):
        snapshot = tmp_path / f"signals__test{i}.snapshot.csv"
        snapshot.write_text(f"timestamp,entry_signal\n{i},0\n")

    result = check_snapshot_directory(tmp_path, max_count=10)

    assert result["pass"] is False
    assert result["count"] == 15
    assert "exceeds recommended limit" in result["message"]


def test_check_snapshot_directory_with_secrets(tmp_path):
    """Test governance check detects secrets in directory."""
    # Create clean snapshot
    snapshot = tmp_path / "signals__test.snapshot.csv"
    snapshot.write_text("timestamp,entry_signal\n1,0\n")

    # Create metadata with secret
    metadata = tmp_path / "signals__test.metadata.json"
    # Build fake secret to avoid GitHub push protection
    fake_secret = "sk_" + "test_" + "verylongsecretkey123456789"
    bad_content = json.dumps({
        "name": "test",
        "secret_key": fake_secret
    })
    metadata.write_text(bad_content)

    result = check_snapshot_directory(tmp_path)

    assert result["pass"] is False
    assert len(result["insecure"]) > 0
    assert "potential secrets" in result["message"]


def test_actual_snapshot_directory_compliance():
    """Test that actual snapshot directory complies with governance rules."""
    snapshot_root = get_snapshot_root()

    if not snapshot_root.exists():
        pytest.skip("Snapshot directory does not exist yet")

    result = check_snapshot_directory(snapshot_root)

    # Log findings for visibility
    if not result["pass"]:
        print(f"\nGovernance check failed: {result['message']}")
        if result["oversized"]:
            print(f"Oversized files: {result['oversized']}")
        if result["insecure"]:
            print(f"Insecure files: {result['insecure']}")

    # Assert compliance
    assert result["pass"], f"Snapshot directory governance check failed: {result['message']}"
    assert len(result["insecure"]) == 0, "Found potential secrets in snapshot files"


def test_fixture_directory_has_no_secrets(tmp_path, monkeypatch):
    """Test that fixture directories don't contain secrets."""
    from paths import get_scenarios_root

    scenarios_root = get_scenarios_root()

    if not scenarios_root.exists():
        pytest.skip("Scenarios directory does not exist yet")

    # Check all metadata.json files in fixture directories
    metadata_files = list(scenarios_root.rglob("metadata.json"))

    if not metadata_files:
        pytest.skip("No fixture metadata files found")

    failures = []
    for metadata_file in metadata_files:
        result = check_snapshot_security(metadata_file)
        if not result["pass"]:
            failures.append({
                "file": str(metadata_file.relative_to(scenarios_root)),
                "findings": result["findings"]
            })

    assert len(failures) == 0, f"Found secrets in {len(failures)} fixture metadata file(s): {failures}"


def test_pre_commit_does_not_update_snapshots():
    """Verify pre-commit configuration runs tests without snapshot update flags.

    This ensures CI/pre-commit hooks enforce snapshot stability rather than
    silently updating them.
    """
    pre_commit_config = Path(".pre-commit-config.yaml")

    if not pre_commit_config.exists():
        pytest.skip("No pre-commit configuration found")

    config_content = pre_commit_config.read_text()

    # Ensure pytest hook doesn't include update flags
    assert "--update-snapshots" not in config_content, "Pre-commit should not auto-update snapshots"
    assert "--auto-create-snapshots" not in config_content, "Pre-commit should not auto-create snapshots"
    assert "SNAPSHOT_UPDATE" not in config_content, "Pre-commit should not set SNAPSHOT_UPDATE env var"
    assert "SNAPSHOT_AUTO_CREATE" not in config_content, "Pre-commit should not set SNAPSHOT_AUTO_CREATE env var"
