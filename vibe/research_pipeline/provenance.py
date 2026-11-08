"""Provenance fields recorded in fixed-rule / paired / condition study definitions.

New definitions carry, in addition to the study parameters:

* ``execution_identity`` -- ``ExecutionRealismConfig.identity()`` of the
  config every run in the study used (entry fill policy, min-risk floor,
  take-profit trade-through, commission/slippage model, execution model
  version, ...).
* ``dataset_manifest_hash`` + ``dataset_files`` -- the input-parquet pin from
  :mod:`vibe.research_pipeline.dataset_pin`.
* ``dataset_id_manifest_sha256`` -- sha256 of the optional curated
  ``--dataset-manifest`` file (symbol -> dataset_id).
* ``provenance_schema_version`` -- marks definitions written with these fields.
* ``definition_sha256`` -- canonical hash of the whole definition (all of the
  above included), so two studies with different execution configs or input
  data never share a definition hash.

Definitions recorded before ``provenance_schema_version`` existed stay
readable: :func:`definition_execution_identity` reconstructs the legacy default
(``at_next_bar_open``: the scripts refused to record any other entry fill
policy), and :func:`definition_dataset_pin` returns ``None`` because legacy
definitions used ``dataset_manifest_hash`` for the *curated manifest file*
hash, not the parquet pin. Experiment ids are uuid7s (no lookup is keyed on a
hash of the definition), so adding fields changes no existing identifiers.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from vibe.research_pipeline.hashing import hash_object

PROVENANCE_SCHEMA_VERSION = 1

LEGACY_ENTRY_FILL_POLICY = "at_next_bar_open"


def apply_provenance(
    definition: dict[str, Any],
    *,
    execution_identity: Optional[Mapping[str, Any]] = None,
    dataset_pin: Optional[Mapping[str, Any]] = None,
    dataset_id_manifest_hash: Optional[str] = None,
) -> dict[str, Any]:
    if execution_identity is not None:
        definition["execution_identity"] = dict(execution_identity)
    if dataset_pin is not None:
        definition.update(dict(dataset_pin))
    if dataset_id_manifest_hash:
        definition["dataset_id_manifest_sha256"] = dataset_id_manifest_hash
    if execution_identity is not None or dataset_pin is not None:
        definition["provenance_schema_version"] = PROVENANCE_SCHEMA_VERSION
        definition["definition_sha256"] = study_definition_hash(definition)
    return definition


def is_legacy_definition(definition: Mapping[str, Any]) -> bool:
    return "provenance_schema_version" not in definition


def definition_execution_identity(
    definition: Mapping[str, Any],
    *,
    execution_model_version: Optional[int] = None,
) -> dict[str, Any]:
    """Execution identity of a study, reconstructing the legacy default.

    For legacy definitions only the fields that the record-time guard pinned
    are reported, tagged ``source: legacy_default``. ``execution_model_version``
    (available from the member runs' fingerprints) fills the v7 defaults of
    ``realistic()`` when given.
    """
    recorded = definition.get("execution_identity")
    if isinstance(recorded, Mapping):
        return {**dict(recorded), "source": "recorded"}
    legacy: dict[str, Any] = {
        "entry_fill_policy": LEGACY_ENTRY_FILL_POLICY,
        "source": "legacy_default",
    }
    if execution_model_version is not None:
        legacy["execution_model_version"] = execution_model_version
        if execution_model_version >= 7:
            legacy["min_risk_bps"] = 5.0
            legacy["take_profit_trade_through_ticks"] = 1
        else:
            legacy["min_risk_bps"] = 0.0
            legacy["take_profit_trade_through_ticks"] = 0
    return legacy


def definition_dataset_pin(definition: Mapping[str, Any]) -> Optional[str]:
    """Parquet-pin hash, or ``None`` for legacy definitions (no pin recorded)."""
    if is_legacy_definition(definition):
        return None
    value = definition.get("dataset_manifest_hash")
    return value if isinstance(value, str) else None


def definition_dataset_id_manifest_hash(definition: Mapping[str, Any]) -> Optional[str]:
    """Curated ``--dataset-manifest`` file hash under either key generation."""
    if is_legacy_definition(definition):
        value = definition.get("dataset_manifest_hash")
    else:
        value = definition.get("dataset_id_manifest_sha256")
    return value if isinstance(value, str) else None


def study_definition_hash(definition: Mapping[str, Any]) -> str:
    """Canonical hash of a whole study definition (provenance included).

    The stored ``definition_sha256`` itself is excluded, so the hash can be
    re-verified from a stored definition.
    """
    return hash_object({k: v for k, v in definition.items() if k != "definition_sha256"})
