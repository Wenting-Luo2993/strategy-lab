import pandas as pd
import pytest


def test_snapshot_workflow_create_update(assert_snapshot, monkeypatch, tmp_path):
    # Override snapshot root to temp dir for isolation
    monkeypatch.setenv("SNAPSHOT_ROOT", str(tmp_path / "snapshots"))
    # Enable auto-create for initial missing snapshot
    monkeypatch.setenv("SNAPSHOT_AUTO_CREATE", "1")
    df = pd.DataFrame({"timestamp": [1,2,3], "value": [10.0, 11.0, 12.0]}).set_index("timestamp")
    assert_snapshot(df, name="basic", kind="signals")  # creates
    # Disable auto-create for subsequent operations
    monkeypatch.delenv("SNAPSHOT_AUTO_CREATE")
    # Modify value to induce diff
    df2 = pd.DataFrame({"timestamp": [1,2,3], "value": [10.0, 11.5, 12.0]}).set_index("timestamp")
    with pytest.raises(AssertionError):
        assert_snapshot(df2, name="basic", kind="signals")
    # Now enable update and verify pass
    monkeypatch.setenv("SNAPSHOT_UPDATE", "1")
    assert_snapshot(df2, name="basic", kind="signals")  # updates existing
    # Subsequent assertion without update should pass
    monkeypatch.delenv("SNAPSHOT_UPDATE")
    assert_snapshot(df2, name="basic", kind="signals")
