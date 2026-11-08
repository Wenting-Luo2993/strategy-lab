import pytest
from pydantic import ValidationError

from vibe.common.ruleset.models import ORBStrategyParams
from vibe.common.strategies.orb import ORBStrategy, ORBStrategyConfig


@pytest.mark.parametrize(
    ("mode", "input_flags", "expected"),
    [
        ("symmetric", (True, False), (True, False)),
        ("symmetric", (False, True), (False, True)),
        ("long_only", (True, True), (True, False)),
        ("long_only", (False, True), (False, False)),
        ("short_only", (True, False), (False, False)),
        ("short_only", (True, True), (False, True)),
    ],
)
def test_direction_mode_filters_breakout_eligibility(mode, input_flags, expected):
    strategy = ORBStrategy(ORBStrategyConfig(name="test", direction_mode=mode))

    assert strategy._apply_direction_mode(*input_flags) == expected


def test_ruleset_direction_mode_defaults_and_validates():
    assert ORBStrategyParams().direction_mode == "symmetric"
    assert ORBStrategyParams(direction_mode="long_only").direction_mode == "long_only"
    with pytest.raises(ValidationError):
        ORBStrategyParams(direction_mode="after_seeing_test")
