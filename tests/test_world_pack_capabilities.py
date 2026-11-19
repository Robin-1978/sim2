"""Every shipped world follows the capability + binding contract."""

from __future__ import annotations

from pathlib import Path

import yaml

from sim2.world.verbs import specific_capability_target

WORLDS = Path(__file__).parents[1] / "worlds"


def _actions() -> list[tuple[Path, dict]]:
    rows: list[tuple[Path, dict]] = []
    for path in sorted(WORLDS.glob("*/actions.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for action in payload.get("actions") or []:
            if isinstance(action, dict) and action.get("id"):
                rows.append((path, action))
    return rows


def test_no_world_action_names_one_instance_of_a_capability() -> None:
    failures = []
    for path, action in _actions():
        target = specific_capability_target(action)
        if target is not None:
            verb, literal = target
            failures.append(f"{path.parent.name}/{action['id']}: {verb} targets {literal!r}")
    assert not failures, "target-specific capabilities must bind a parameter:\n" + "\n".join(failures)


def test_every_world_action_declares_its_capability_family() -> None:
    failures = [
        f"{path.parent.name}/{action['id']}"
        for path, action in _actions()
        if not str(action.get("verb") or "").strip()
    ]
    assert not failures, "world Actions must declare verb explicitly:\n" + "\n".join(failures)
