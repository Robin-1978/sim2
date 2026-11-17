from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from sim2.agents.session import model_tool_result
from sim2.api.service import ControlService, build_player_view
from sim2.bootstrap.service import (
    create_instance_from_template,
    validate_config,
    validate_world_template,
    world_package_template,
    write_world_package_from_template,
)
from sim2.world.schema import AttributeSchema
from sim2.world.ui_text import UI_TEXT_KEYS, UiText, validate_ui_text

ROOT = Path(__file__).parents[1]
WORLDS = ROOT / "worlds"


def _orbital_template() -> dict:
    return yaml.safe_load((ROOT / "examples" / "orbital-world-template.yaml").read_text(encoding="utf-8"))


def test_state_ids_are_the_declared_state_attributes_only():
    schema = AttributeSchema([
        {"id": "integrity", "kind": "state"},
        {"id": "signal_clarity", "kind": "need"},
        {"id": "charge", "kind": "state"},
    ])
    assert schema.state_ids() == ["integrity", "charge"]
    assert AttributeSchema().state_ids() == []


def test_tool_result_forwards_the_worlds_declared_state_attributes_and_no_other_state():
    result = {"status": "ok", "agent": {"initial_state": {"status": "idle", "charge": 90, "private_note": "hidden"}}}

    declared = model_tool_result("inspect", result, state_attributes=["charge"])
    assert declared["agent"]["initial_state"] == {"status": "idle", "charge": 90}
    # No attribute name is built into the projection: undeclared state is not forwarded.
    assert model_tool_result("inspect", result)["agent"]["initial_state"] == {"status": "idle"}


def test_every_neutral_default_is_a_valid_phrase():
    assert validate_ui_text({key: default for key, (default, _fields) in UI_TEXT_KEYS.items()}) == []


def test_ui_text_falls_back_to_neutral_english_and_ignores_unusable_phrases():
    assert UiText().text("travel_choice", name="Dock") == "Travel to Dock"
    assert UiText.from_setting(None).text("world_label") == "The world"
    assert UiText.from_setting({"ui_text": "not an object"}).text("world_label") == "The world"

    ui = UiText({"travel_choice": "前往 {name}", "travel_time": "{secret}", "world_label": "  ", "unknown": "x"})
    assert ui.text("travel_choice", name="Dock") == "前往 Dock"
    # A phrase with a placeholder its key does not offer, or an empty one, is not used.
    assert ui.text("travel_time", minutes=5) == "5 minutes"
    assert ui.text("world_label") == "The world"


def test_ui_text_validation_reports_unknown_keys_bad_placeholders_and_empty_phrases():
    assert validate_ui_text(None) == []
    assert validate_ui_text({"travel_choice": "Go to {name}", "world_label": "The relay"}) == []
    assert validate_ui_text("x") == ["ui_text must be an object"]

    errors = validate_ui_text({
        "travel_chioce": "x",
        "travel_time": "in {mins}",
        "travel_choice": "{name.upper}",
        "travel_summary": "",
        "world_label": "{",
    })
    assert any("ui_text.travel_chioce is not a known key" in error for error in errors)
    assert any("ui_text.travel_time may only use {minutes}" in error for error in errors)
    assert any("ui_text.travel_choice may only use {name}" in error for error in errors)
    assert any("ui_text.travel_summary must be a non-empty string" in error for error in errors)
    assert any("ui_text.world_label is not a valid phrase" in error for error in errors)


def test_shipped_worlds_declare_valid_ui_text_and_chinese_worlds_keep_their_wording():
    worlds = sorted(path for path in WORLDS.iterdir() if (path / "setting.yaml").is_file())
    assert {"starter_village", "aster_relay"} <= {path.name for path in worlds}
    for world in worlds:
        setting = yaml.safe_load((world / "setting.yaml").read_text(encoding="utf-8"))
        assert validate_ui_text(setting.get("ui_text")) == [], world.name
        if str(setting.get("locale", "")).startswith("zh"):
            assert UiText.from_setting(setting).text("travel_choice", name="村广场") == "前往 村广场", world.name


def test_package_validation_reports_a_broken_ui_text(tmp_path: Path):
    package = tmp_path / "relay"
    shutil.copytree(WORLDS / "aster_relay", package)
    setting_path = package / "setting.yaml"
    setting = yaml.safe_load(setting_path.read_text(encoding="utf-8"))
    setting["ui_text"] = {"travel_choice": "Go to {place}"}
    setting_path.write_text(yaml.safe_dump(setting, allow_unicode=True), encoding="utf-8")

    errors = validate_config(package)["errors"]

    assert any("ui_text.travel_choice may only use {name}" in error for error in errors)


def test_template_validation_reports_a_broken_ui_text_and_the_round_trip_keeps_a_valid_one(tmp_path: Path):
    template = _orbital_template()
    template["world"]["ui_text"] = {"travel_choice": "Go to {place}"}
    report = validate_world_template(template)
    assert report["valid"] is False
    assert any("world.ui_text.travel_choice may only use {name}" in error for error in report["errors"])

    template["world"]["ui_text"] = {"travel_choice": "Fly to {name}", "world_label": "The relay"}
    assert validate_world_template(template) == {"valid": True, "errors": []}
    package = write_world_package_from_template(template, tmp_path / "orbital")
    assert yaml.safe_load((package / "setting.yaml").read_text(encoding="utf-8"))["ui_text"] == template["world"]["ui_text"]
    assert world_package_template(package)["world"]["ui_text"] == template["world"]["ui_text"]


def test_travel_text_and_world_label_come_from_the_world_not_from_the_platform(tmp_path: Path):
    neutral_db = create_instance_from_template(_orbital_template(), "neutral-text", tmp_path / "runtime")
    declared = _orbital_template()
    declared["world"]["ui_text"] = {
        "travel_choice": "Fly to {name}",
        "travel_summary": "Burn along the known lane to {name}.",
        "travel_time": "{minutes} min",
        "world_label": "The relay",
    }
    declared_db = create_instance_from_template(declared, "declared-text", tmp_path / "runtime")

    neutral = ControlService(neutral_db)
    try:
        travel = next(item for item in build_player_view(neutral.platform, "unit_seven")["actions"] if item.get("kind") == "travel")
        assert travel["choice"] == travel["name"] == "Travel to Docking Ring"
        assert travel["summary"] == "Follow the known route to Docking Ring."
        assert travel["purpose"] == "Move to another known place"
        assert travel["impact"] == ["Arrive at the selected location"]
        assert travel["time"].endswith(" minutes")
        assert travel["parameters"] == {"location_id": "docking_ring"}
        signal = neutral.inject("observer.signal", {"summary": "The relay hums."})
        assert neutral.event_related(signal["event_id"])["event"]["actor"] == "The world"
    finally:
        neutral.close()

    service = ControlService(declared_db)
    try:
        travel = next(item for item in build_player_view(service.platform, "unit_seven")["actions"] if item.get("kind") == "travel")
        assert travel["choice"] == "Fly to Docking Ring"
        assert travel["summary"] == "Burn along the known lane to Docking Ring."
        assert travel["time"].endswith(" min")
        # Phrases the world did not declare keep the neutral wording.
        assert travel["impact"] == ["Arrive at the selected location"]
        signal = service.inject("observer.signal", {"summary": "The relay hums."})
        assert service.event_related(signal["event_id"])["event"]["actor"] == "The relay"
    finally:
        service.close()
