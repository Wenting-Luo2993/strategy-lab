"""Tests for the final out-of-sample holdout lock.

The lock's value rests entirely on the guard actually refusing data. A test
suite that only checked the lock *parses* would pass just as happily against a
guard that returned holdout bars to anyone who asked, so the emphasis here is
on the refusal path, on the clamp, and on the access log being append-only.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

from vibe.research_pipeline.holdout import (
    AcceptanceRule,
    HoldoutLock,
    HoldoutViolation,
    UnlockToken,
    load_lock,
    read_access_log,
    record_access,
    touch_count,
)

NY = ZoneInfo("America/New_York")


def _rule(**over) -> AcceptanceRule:
    kwargs = dict(
        metric="expectancy_r", comparison=">=", threshold=0.10, min_trades=200
    )
    kwargs.update(over)
    return AcceptanceRule(**kwargs)


def _lock(**over) -> HoldoutLock:
    kwargs = dict(
        dev_end=date(2024, 12, 31),
        oos_start=date(2025, 1, 1),
        oos_end=date(2026, 4, 27),
        symbols=("QQQ", "MSFT"),
        declared_at=date(2026, 9, 9),
        declared_by="tester",
        acceptance=_rule(),
    )
    kwargs.update(over)
    return HoldoutLock(**kwargs)


def _token(**over) -> UnlockToken:
    kwargs = dict(
        reason="final evaluation of the frozen ORB methodology",
        requested_by="tester",
        run_id="run-001",
    )
    kwargs.update(over)
    return UnlockToken(**kwargs)


@pytest.fixture
def log_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "oos_access_log.jsonl"
    monkeypatch.setenv("RESEARCH__HOLDOUT_ACCESS_LOG", str(path))
    return path


class TestCommittedLock:
    """The lock that ships in the repository must be real and coherent."""

    def test_repository_lock_loads(self):
        lock = load_lock()
        assert lock.dev_end < lock.oos_start <= lock.oos_end
        assert lock.symbols

    def test_repository_lock_boundary_matches_prior_research(self):
        # Every record in research/ was produced over 2018-01-01..2024-12-31.
        # If dev_end ever moves earlier than that, the holdout would be
        # claiming to be untouched over sessions that demonstrably were.
        assert load_lock().dev_end >= date(2024, 12, 31)

    def test_acceptance_rule_is_pre_registered(self):
        acc = load_lock().acceptance
        assert acc.metric and acc.threshold is not None
        assert acc.min_trades > 0, (
            "A rule with no minimum sample size can be satisfied by noise."
        )


class TestLockHash:
    def test_hash_is_deterministic(self):
        assert _lock().lock_hash == _lock().lock_hash

    def test_hash_is_order_independent_for_symbols(self):
        a = _lock(symbols=("QQQ", "MSFT"))
        b = _lock(symbols=("MSFT", "QQQ"))
        assert a.lock_hash == b.lock_hash

    @pytest.mark.parametrize(
        "field,value",
        [
            ("dev_end", date(2024, 6, 30)),
            ("oos_start", date(2025, 2, 1)),
            ("oos_end", date(2026, 1, 1)),
            ("symbols", ("QQQ",)),
        ],
    )
    def test_hash_changes_when_range_changes(self, field, value):
        assert _lock(**{field: value}).lock_hash != _lock().lock_hash

    def test_hash_changes_when_acceptance_threshold_changes(self):
        # Moving the bar is as much a redefinition of the test as moving the
        # dates. If the hash ignored it, the criterion could drift invisibly.
        moved = _lock(acceptance=_rule(threshold=0.01))
        assert moved.lock_hash != _lock().lock_hash

    def test_hash_ignores_cosmetic_metadata(self):
        # Who declared it, and the rationale prose, do not change what is
        # being tested; churning the hash on those would make it useless as
        # an identity for the range.
        other = _lock(declared_by="someone-else", acceptance=_rule(rationale="x"))
        assert other.lock_hash == _lock().lock_hash


class TestLockValidation:
    def test_overlapping_ranges_rejected(self):
        with pytest.raises(ValueError, match="must be after dev_end"):
            _lock(oos_start=date(2024, 6, 1))

    def test_inverted_oos_rejected(self):
        with pytest.raises(ValueError, match="precedes"):
            _lock(oos_start=date(2026, 1, 1), oos_end=date(2025, 1, 1))

    def test_empty_symbol_list_rejected(self):
        with pytest.raises(ValueError, match="guards nothing"):
            _lock(symbols=())

    def test_bad_comparison_rejected(self):
        with pytest.raises(ValueError, match="Unsupported comparison"):
            _rule(comparison="~=")


class TestUnlockToken:
    @pytest.mark.parametrize("field", ["reason", "requested_by", "run_id"])
    def test_blank_fields_rejected(self, field):
        with pytest.raises(ValueError, match=field):
            _token(**{field: "  "})

    def test_placeholder_reason_rejected(self):
        with pytest.raises(ValueError, match="real explanation"):
            _token(reason="why")


class TestGuard:
    """The refusal path. If these pass vacuously the lock is decoration."""

    def test_dev_range_allowed(self):
        _lock().assert_within_dev(
            symbol="QQQ", start=date(2019, 1, 1), end=date(2024, 12, 31)
        )

    def test_request_into_holdout_refused(self):
        with pytest.raises(HoldoutViolation, match="final holdout begins"):
            _lock().assert_within_dev(
                symbol="QQQ", start=date(2025, 1, 1), end=date(2025, 6, 1)
            )

    def test_straddling_request_refused(self):
        # The upper bound is what matters. A 2019->2026 request touches the
        # holdout just as surely as one confined to 2025, and checking only
        # `start` would wave this exact shape through.
        with pytest.raises(HoldoutViolation):
            _lock().assert_within_dev(
                symbol="QQQ", start=date(2019, 1, 1), end=date(2026, 1, 1)
            )

    def test_one_day_past_dev_end_refused(self):
        with pytest.raises(HoldoutViolation):
            _lock().assert_within_dev(
                symbol="QQQ", start=date(2024, 1, 1), end=date(2025, 1, 1)
            )

    def test_exactly_dev_end_allowed(self):
        _lock().assert_within_dev(
            symbol="QQQ", start=date(2024, 1, 1), end=date(2024, 12, 31)
        )

    def test_unlocked_symbol_passes_through(self):
        # The lock promises something about the symbols it names. Blocking
        # others would be an unrelated restriction wearing this one's clothes.
        _lock().assert_within_dev(
            symbol="SPY", start=date(2025, 1, 1), end=date(2026, 1, 1)
        )

    def test_token_permits_access(self, log_path):
        _lock().assert_within_dev(
            symbol="QQQ",
            start=date(2025, 1, 1),
            end=date(2025, 6, 1),
            token=_token(),
        )
        assert len(read_access_log(log_path)) == 1


class TestAccessLog:
    def test_access_is_recorded_with_full_attribution(self, log_path):
        lock = _lock()
        record_access(
            lock=lock,
            token=_token(),
            symbol="QQQ",
            start=date(2025, 1, 1),
            end=date(2025, 6, 1),
        )
        (row,) = read_access_log(log_path)
        assert row["symbol"] == "QQQ"
        assert row["run_id"] == "run-001"
        assert row["requested_by"] == "tester"
        assert row["reason"].startswith("final evaluation")
        assert row["lock_hash"] == lock.lock_hash
        assert row["timestamp"]

    def test_log_is_append_only(self, log_path):
        lock = _lock()
        for i in range(3):
            record_access(
                lock=lock,
                token=_token(run_id=f"run-{i}"),
                symbol="QQQ",
                start=date(2025, 1, 1),
                end=date(2025, 6, 1),
            )
        rows = read_access_log(log_path)
        assert [r["run_id"] for r in rows] == ["run-0", "run-1", "run-2"], (
            "Earlier accesses must survive later ones; a log that overwrote "
            "them would let a second look at the holdout hide the first."
        )

    def test_touch_count_filters_by_lock_hash(self, log_path):
        # Redefining the lock must not launder a used holdout by resetting the
        # counter. The count under the current definition and the count over
        # all time are different questions.
        original, moved = _lock(), _lock(oos_end=date(2026, 3, 1))
        record_access(
            lock=original, token=_token(), symbol="QQQ",
            start=date(2025, 1, 1), end=date(2025, 6, 1),
        )
        record_access(
            lock=moved, token=_token(), symbol="QQQ",
            start=date(2025, 1, 1), end=date(2025, 6, 1),
        )
        assert touch_count(path=log_path) == 2
        assert touch_count(lock_hash=original.lock_hash, path=log_path) == 1

    def test_missing_log_reads_as_empty(self, tmp_path):
        assert read_access_log(tmp_path / "nope.jsonl") == []


class TestAcceptanceRule:
    def test_passes_above_threshold(self):
        out = _rule().evaluate(0.15, n_trades=250)
        assert out.passed and out.conclusive

    def test_fails_below_threshold(self):
        out = _rule().evaluate(0.02, n_trades=250)
        assert not out.passed and out.conclusive

    def test_thin_sample_is_inconclusive_not_a_failure(self):
        # A sample too small to say anything has not rejected the strategy;
        # recording it as a rejection would burn the holdout on a result that
        # never had the power to produce a verdict.
        out = _rule().evaluate(0.50, n_trades=12)
        assert not out.passed
        assert not out.conclusive
        assert "below the pre-registered minimum" in out.detail


class TestLoaderIntegration:
    """The guard must bind to the real loader, not just exist beside it."""

    @pytest.fixture
    def parquet_dir(self, tmp_path):
        idx = pd.date_range("2024-12-28 09:30", "2025-01-05 16:00", freq="1h", tz=NY)
        pd.DataFrame(
            {
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.5, "volume": 1000.0,
            },
            index=idx,
        ).to_parquet(tmp_path / "QQQ.parquet")
        return tmp_path

    @pytest.fixture
    def lock_file(self, tmp_path, monkeypatch):
        path = tmp_path / "final_holdout.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "dev_end": "2024-12-31",
                    "oos_start": "2025-01-01",
                    "oos_end": "2026-04-27",
                    "symbols": ["QQQ"],
                    "declared_at": "2026-09-09",
                    "declared_by": "tester",
                    "acceptance": {
                        "metric": "expectancy_r",
                        "comparison": ">=",
                        "threshold": 0.10,
                        "min_trades": 200,
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("RESEARCH__HOLDOUT_CONFIG", str(path))
        return path

    def _loader(self, parquet_dir, **kw):
        from vibe.backtester.data.parquet_loader import ParquetLoader

        return ParquetLoader(parquet_dir, ["QQQ"], **kw)

    @pytest.mark.asyncio
    async def test_explicit_holdout_request_refused(
        self, parquet_dir, lock_file, log_path
    ):
        loader = self._loader(parquet_dir)
        with pytest.raises(HoldoutViolation):
            await loader.get_bars(
                "QQQ",
                start_time=datetime(2024, 12, 28, tzinfo=NY),
                end_time=datetime(2025, 1, 5, tzinfo=NY),
            )

    @pytest.mark.asyncio
    async def test_dev_request_returns_bars(self, parquet_dir, lock_file, log_path):
        loader = self._loader(parquet_dir)
        df = await loader.get_bars(
            "QQQ",
            start_time=datetime(2024, 12, 28, tzinfo=NY),
            end_time=datetime(2024, 12, 31, tzinfo=NY),
        )
        assert not df.empty
        assert df.index.max().date() <= date(2024, 12, 31)

    @pytest.mark.asyncio
    async def test_unbounded_request_is_clamped_not_refused(
        self, parquet_dir, lock_file, log_path
    ):
        # The caller expressed no intent. Returning everything would
        # contaminate silently; raising would break every legitimate
        # "load all development data" call. Clamping is the only option
        # that cannot leak.
        loader = self._loader(parquet_dir)
        df = await loader.get_bars("QQQ")
        assert not df.empty
        assert df.index.max().date() <= date(2024, 12, 31)

    def test_get_full_df_is_also_clamped(self, parquet_dir, lock_file, log_path):
        # A method that bypassed the guard would be an unlocked back door
        # beside a locked front one.
        df = self._loader(parquet_dir).get_full_df("QQQ")
        assert df.index.max().date() <= date(2024, 12, 31)

    @pytest.mark.asyncio
    async def test_token_unlocks_loader_and_logs_once(
        self, parquet_dir, lock_file, log_path
    ):
        loader = self._loader(parquet_dir, holdout_token=_token())
        df = await loader.get_bars(
            "QQQ",
            start_time=datetime(2024, 12, 28, tzinfo=NY),
            end_time=datetime(2025, 1, 5, tzinfo=NY),
        )
        assert df.index.max().date() > date(2024, 12, 31), (
            "An unlocked read must actually return the reserved bars, "
            "otherwise the token is theatre."
        )
        assert len(read_access_log(log_path)) == 1

    @pytest.mark.asyncio
    async def test_enforcement_can_be_disabled_explicitly(
        self, parquet_dir, lock_file, log_path
    ):
        loader = self._loader(parquet_dir, enforce_holdout=False)
        df = await loader.get_bars("QQQ")
        assert df.index.max().date() > date(2024, 12, 31)
