"""Tests for canonical encoding and hashing determinism."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from vibe.research_pipeline.hashing import (
    NonCanonicalValueError,
    canonical_json,
    hash_file,
    hash_object,
    sha256_hex,
)


class _Colour(str, Enum):
    RED = "red"


def test_dict_insertion_order_does_not_change_hash():
    a = {"alpha": 1, "beta": 2, "gamma": 3}
    b = {"gamma": 3, "beta": 2, "alpha": 1}
    assert hash_object(a) == hash_object(b)


def test_nested_dict_order_does_not_change_hash():
    a = {"outer": {"x": [1, {"p": 1, "q": 2}], "y": 2}}
    b = {"outer": {"y": 2, "x": [1, {"q": 2, "p": 1}]}}
    assert hash_object(a) == hash_object(b)


def test_list_order_does_change_hash():
    """Sequence order is meaningful and must not be normalized away."""
    assert hash_object([1, 2, 3]) != hash_object([3, 2, 1])


def test_set_order_does_not_change_hash():
    """Set iteration order is not stable across processes, so it is sorted."""
    assert hash_object({1, 2, 3}) == hash_object({3, 1, 2})


def test_tuple_and_list_encode_identically():
    assert hash_object((1, 2)) == hash_object([1, 2])


def test_negative_zero_matches_positive_zero():
    assert hash_object(-0.0) == hash_object(0.0)


def test_enum_encodes_as_value():
    assert hash_object(_Colour.RED) == hash_object("red")


def test_enum_dict_key_encodes_as_value():
    assert hash_object({_Colour.RED: 1}) == hash_object({"red": 1})


def test_equivalent_datetimes_in_different_zones_hash_equal():
    utc = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
    plus_two = utc.astimezone(timezone(timedelta(hours=2)))
    assert hash_object(utc) == hash_object(plus_two)


def test_naive_datetime_is_rejected():
    with pytest.raises(NonCanonicalValueError, match="naive datetime"):
        hash_object(datetime(2026, 3, 2, 14, 30))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_are_rejected(bad):
    """A non-finite value in an identity payload signals a broken calculation."""
    with pytest.raises(NonCanonicalValueError, match="non-finite"):
        hash_object(bad)


def test_windows_and_posix_paths_hash_equal():
    """The same repo on Windows and Linux must produce the same hash."""
    assert hash_object(PureWindowsPath("a/b/c.yaml")) == hash_object(
        PurePosixPath("a/b/c.yaml")
    )


def test_decimal_normalizes():
    assert hash_object(Decimal("1.10")) == hash_object(Decimal("1.1"))


def test_date_is_supported():
    assert hash_object(date(2026, 1, 2)) == hash_object(date(2026, 1, 2))


def test_unknown_type_is_rejected_rather_than_repr_hashed():
    class Opaque:
        pass

    with pytest.raises(NonCanonicalValueError, match="No canonical encoding"):
        hash_object(Opaque())


def test_duplicate_keys_after_coercion_are_rejected():
    with pytest.raises(NonCanonicalValueError, match="Duplicate key"):
        hash_object({1: "a", "1": "b"})


def test_canonical_json_is_compact_and_sorted():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_hash_is_stable_across_calls():
    payload = {"strategy": "orb", "params": {"n": 5, "x": 1.5}}
    assert hash_object(payload) == hash_object(payload)


def test_sha256_hex_matches_known_vector():
    assert sha256_hex("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_hash_file_matches_content_hash(tmp_path):
    target = tmp_path / "ruleset.yaml"
    target.write_bytes(b"abc")
    assert hash_file(target) == sha256_hex(b"abc")


def test_hash_file_detects_content_change_with_same_name(tmp_path):
    """The defect this fixes: caching keyed on filename, not content."""
    target = tmp_path / "ruleset.yaml"
    target.write_text("stop_loss: 1.0")
    before = hash_file(target)
    target.write_text("stop_loss: 2.0")
    assert hash_file(target) != before
