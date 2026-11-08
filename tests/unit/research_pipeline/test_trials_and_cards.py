"""S6: trial ledger and hypothesis cards."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml
from statistics import NormalDist

from vibe.research_pipeline.hypothesis_cards import HypothesisCard, load_cards
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.trials import TrialTag

REPO = Path(__file__).resolve().parents[3]


def _definition(programme, phase="exploration", family="condition", n=1):
    trial = TrialTag(
        programme=programme, phase=phase, family=family, label="x", n_trials=n
    )
    return {"trial": trial.model_dump(mode="json")}


def test_trial_counts_by_phase_and_family(tmp_path):
    store = SqliteResearchStore(tmp_path / "r.sqlite3")
    try:
        a = store.start_experiment("a", _definition("p", "exploration", "condition", 3))
        store.finish_experiment(a, "completed", {})
        b = store.start_experiment("b", _definition("p", "exploration", "enhancement", 1))
        store.finish_experiment(b, "execution_failed", {})
        store.start_experiment("c", _definition("p", "confirmation", "baseline", 2))  # running
        d = store.start_experiment("d", _definition("other", n=9))
        store.finish_experiment(d, "completed", {})
        store.start_experiment("untagged", {"kind": "x"})
        counts = store.trial_counts("p")
    finally:
        store.close()
    assert counts["total_trials"] == 6
    assert counts["by_phase"] == {"confirmation": 2, "exploration": 4}
    assert counts["by_family"] == {"baseline": 2, "condition": 3, "enhancement": 1}
    assert {(c["phase"], c["family"], c["trials"]) for c in counts["cells"]} == {
        ("exploration", "condition", 3),
        ("exploration", "enhancement", 1),
        ("confirmation", "baseline", 2),
    }
    empty = SqliteResearchStore(tmp_path / "e.sqlite3")
    try:
        assert empty.trial_counts("p")["total_trials"] == 0
    finally:
        empty.close()


def test_ledger_script_prints_table(tmp_path, capsys):
    from scripts.show_trial_ledger import main

    path = tmp_path / "r.sqlite3"
    store = SqliteResearchStore(path)
    try:
        store.start_experiment("a", _definition("p", n=2))
    finally:
        store.close()
    assert main(["--programme", "p", "--research-db", str(path)]) == 0
    out = capsys.readouterr().out
    assert "exploration" in out and "condition" in out


def test_runner_clis_store_trial_tag_only_with_programme():
    from scripts import run_fixed_rule_study as fixed
    from scripts import run_paired_comparison as paired

    for module, extra in (
        (fixed, ["--ruleset", "r"]),
        (paired, ["--baseline-ruleset", "a", "--variant-ruleset", "b", "--lever", "l"]),
    ):
        common = ["--title", "t", "--symbols", "A", "--start", "2020-01-01",
                  "--end", "2020-02-01", "--evidence-role", "exploration"]
        args = module._parse_args(common + extra)
        assert args.programme is None
        args = module._parse_args(common + extra + ["--programme", "p"])
        assert args.programme == "p"


def test_fixed_rule_cli_sets_n_trials_from_conditions():
    from vibe.research_pipeline.fixed_rule import FixedRuleSpec  # noqa: F401

    tag = TrialTag(
        programme="p", phase="exploration", family="condition", label="x", n_trials=2
    )
    assert tag.model_dump(mode="json")["n_trials"] == 2
    with pytest.raises(ValueError):
        TrialTag(programme="p", phase="exploration", family="condition",
                 label="x", n_trials=0)


def _card(**kw):
    base = dict(
        card_id="H1", programme="p", claim="c", rule="r", prediction="pr",
        alpha_share=0.05 / 3, analysis="a", expected_n_trades=1600,
        expected_sd_r=1.0, kill_criteria=["k"], confirmation_data="d",
        status="registered",
    )
    base.update(kw)
    return HypothesisCard(**base)


def test_planned_mde_matches_hand_calculation():
    card = _card(expected_design_effect=1.5)
    se = 1.0 * math.sqrt(1.5 / 1600)
    expected = (NormalDist().inv_cdf(1 - card.alpha_share / 2) + NormalDist().inv_cdf(0.8)) * se
    assert card.planned_mde_r() == pytest.approx(expected, rel=1e-6)


def _write(directory: Path, cards):
    for card in cards:
        (directory / f"{card.card_id}.yaml").write_text(
            yaml.safe_dump(card.model_dump(mode="json")), encoding="utf-8"
        )


def test_load_cards_rejects_alpha_over_budget(tmp_path):
    _write(tmp_path, [_card(card_id="H1", alpha_share=0.03),
                      _card(card_id="H2", alpha_share=0.03)])
    with pytest.raises(ValueError, match="alpha"):
        load_cards(tmp_path)


def test_load_cards_accepts_budget_and_ignores_other_status(tmp_path):
    _write(tmp_path, [_card(card_id="H1", alpha_share=0.03),
                      _card(card_id="H2", alpha_share=0.02),
                      _card(card_id="H3", alpha_share=0.05, status="draft")])
    assert len(load_cards(tmp_path)) == 3


def test_resolved_cards_still_spend_alpha(tmp_path):
    _write(tmp_path, [_card(card_id="H1", alpha_share=0.03, status="supported"),
                      _card(card_id="H2", alpha_share=0.03, status="registered")])
    with pytest.raises(ValueError, match="alpha"):
        load_cards(tmp_path)


def test_example_card_loads():
    cards = load_cards(REPO / "config" / "research" / "hypothesis_cards")
    assert [c.card_id for c in cards] == ["EXAMPLE"]
    assert cards[0].status == "example"
    assert cards[0].planned_mde_r() > 0
