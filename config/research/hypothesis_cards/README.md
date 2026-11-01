# Hypothesis cards

One YAML file per pre-registered hypothesis, validated by
`vibe/research_pipeline/hypothesis_cards.py` (`HypothesisCard`, `load_cards`).

- Write a card **before** confirmation data is examined. Cards H1 to H3 are
  written after exploration (research Step 5), not before.
- `alpha_share` values of `registered` cards in one programme must sum to at
  most 0.05.
- `planned_mde_r()` shows the smallest effect the planned sample can detect.
- `EXAMPLE.yaml` has status `example` and is not a real hypothesis.
