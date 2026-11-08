"""Tests for RunFingerprint sensitivity.

Each test asserts that changing one input changes the identity. Together they
close the hole in the existing sweep cache, which keys on the ruleset *filename*
and therefore silently reuses results computed under different rules.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vibe.research_pipeline.identity import RunFingerprint

BASE = dict(
    strategy_id="orb",
    ruleset_content_sha256="a" * 64,
    parameters={"opening_range_minutes": 15, "stop_atr_mult": 1.5},
    universe_hash="b" * 64,
    split_manifest_hash="c" * 64,
    data_snapshot_id="databento-2026-09-01",
    feature_set_version=1,
    execution_model_version=1,
    metric_calculation_version=1,
    code_commit="1234567abcdef",
    code_dirty=False,
)


def _fp(**overrides):
    return RunFingerprint(**{**BASE, **overrides})


def test_identical_inputs_produce_identical_fingerprints():
    assert _fp().fingerprint == _fp().fingerprint


def test_parameter_insertion_order_does_not_matter():
    a = _fp(parameters={"opening_range_minutes": 15, "stop_atr_mult": 1.5})
    b = _fp(parameters={"stop_atr_mult": 1.5, "opening_range_minutes": 15})
    assert a.fingerprint == b.fingerprint


@pytest.mark.parametrize(
    "field,value",
    [
        ("strategy_id", "orb_v2"),
        ("ruleset_content_sha256", "d" * 64),
        ("parameters", {"opening_range_minutes": 30}),
        ("universe_hash", "e" * 64),
        ("split_manifest_hash", "f" * 64),
        ("data_snapshot_id", "databento-2026-10-01"),
        ("feature_set_version", 2),
        ("execution_model_version", 2),
        ("metric_calculation_version", 2),
        ("code_commit", "fedcba9876543"),
        ("code_dirty", True),
        ("random_seed", 42),
    ],
)
def test_every_input_changes_the_fingerprint(field, value):
    assert _fp(**{field: value}).fingerprint != _fp().fingerprint


def test_ruleset_content_change_changes_identity_even_with_same_name():
    """The exact defect in parameter_sweep._cache_key.

    The old key hashed base_ruleset_path.name, so editing a non-swept field of
    a ruleset returned a stale cached pickle.
    """
    edited = _fp(ruleset_content_sha256="9" * 64)
    assert edited.fingerprint != _fp().fingerprint


def test_execution_model_version_is_part_of_identity():
    """Changing intrabar exit ordering must invalidate every cached result."""
    assert _fp(execution_model_version=2).fingerprint != _fp().fingerprint


def test_cache_key_equals_fingerprint():
    """A cache must not key on a narrower tuple than determines the answer."""
    fp = _fp()
    assert fp.cache_key() == fp.fingerprint


def test_dirty_tree_is_not_reproducible():
    assert _fp(code_dirty=False).is_reproducible
    assert not _fp(code_dirty=True).is_reproducible


def test_fingerprint_is_hex_sha256():
    fingerprint = _fp().fingerprint
    assert len(fingerprint) == 64
    assert all(c in "0123456789abcdef" for c in fingerprint)


def test_non_hex_commit_rejected():
    with pytest.raises(ValidationError, match="hex git commit"):
        _fp(code_commit="not-a-commit")


def test_non_hex_digest_rejected():
    with pytest.raises(ValidationError, match="hex SHA-256"):
        _fp(universe_hash="z" * 64)


def test_fingerprint_is_frozen():
    fp = _fp()
    with pytest.raises(ValidationError):
        fp.strategy_id = "other"


def test_unknown_field_rejected():
    """Forbidding extras stops a new input silently escaping the identity."""
    with pytest.raises(ValidationError):
        _fp(some_new_input="value")


def test_hash_ruleset_reads_file_content(tmp_path):
    path = tmp_path / "orb.yaml"
    path.write_text("opening_range_minutes: 15")
    first = RunFingerprint.hash_ruleset(path)
    path.write_text("opening_range_minutes: 30")
    assert RunFingerprint.hash_ruleset(path) != first
