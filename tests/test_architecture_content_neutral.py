"""Platform code must not hardcode probe or world content identifiers.

Phase 1/2 primitives are generic: ``resource_withdrawal``, ``resource_take``,
``authorization``, ``violation`` and their event families. The probe world owns
the concrete names such as ``energy_reserve``, ``take_energy`` and
``approve_withdrawal``. This guard fails if those names leak back into
``src/sim2``.
"""

from __future__ import annotations

from pathlib import Path
import re

import yaml

SRC = Path(__file__).parents[1] / "src" / "sim2"
WORLD = Path(__file__).parents[1] / "worlds" / "social_authority_probe"

# Probe and planned Phase 2/3 content. Framework vocabulary such as ``travel``,
# ``player``, ``institution_allocation`` and condition paths such as
# ``patient.in_clinic`` is intentionally excluded: those are generic engine
# features, not one world's identifiers.
CONTENT_IDS = (
    "energy_reserve",
    "withdraw_energy",
    "take_energy",
    "mira",
    "tomas",
    "relay_station",
    "relay_yard",
    "approve_withdrawal",
    "reject_withdrawal",
    "request_access",
    "revoke_delegation",
    "water_reserve",
    "water_plant",
    "withdraw_water",
    "take_water",
    "fill_water_reserve",
    "review_water_violation",
    "water_reserve_stewardship",
    "water_approval_authority",
    "water_revocation_authority",
    "water_violation_authority",
    "granary",
    "old_well",
    "village_square",
    "gather_water",
)


def test_platform_source_has_no_probe_content_identifiers() -> None:
    hits: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for identifier in CONTENT_IDS:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])", text, re.IGNORECASE):
                hits.append(f"{path.relative_to(SRC)}: {identifier}")
    assert not hits, "Platform source contains content identifiers:\n" + "\n".join(hits)


def test_probe_authorization_operations_are_generic() -> None:
    payload = yaml.safe_load((WORLD / "behavior_trees.yaml").read_text(encoding="utf-8")) or {}
    operations = {
        str(item["authorization_operation"])
        for item in payload.get("behavior_trees", []) or []
        if isinstance(item, dict) and item.get("authorization_operation")
    }
    assert operations <= {
        "open_access_request",
        "decide_access_request",
        "revoke_access",
        "review_violation",
    }
