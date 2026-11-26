from contextlib import closing
from pathlib import Path

import pytest

from sim2.bootstrap.service import create_instance, validate_config, world_package_template, write_world_package_from_template
from sim2.bootstrap.template_validation import validate_world_template
from sim2.platform.runtime import Platform


WORLDS = sorted(path.parent for path in (Path(__file__).parents[1] / "worlds").glob("*/manifest.yaml"))


@pytest.mark.parametrize("source", WORLDS, ids=lambda path: path.name)
def test_existing_world_uses_the_same_contract_after_export(tmp_path, source):
    template = world_package_template(source)
    report = validate_world_template(template)
    assert report["valid"], report["errors"]
    exported = write_world_package_from_template(template, tmp_path / "exported")
    assert validate_config(exported)["valid"]
    assert world_package_template(exported) == template

    if source.name in {"windward_skyfall", "tidebound_commons"}:
        with closing(Platform(create_instance(source, "original", tmp_path))) as original:
            with closing(Platform(create_instance(exported, "imported", tmp_path))) as imported:
                assert imported.store.repositories.metadata.get("world_properties") == original.store.repositories.metadata.get("world_properties")
                assert imported.store.repositories.metadata.get("setting")["ui"] == original.store.repositories.metadata.get("setting")["ui"]
                actor_id = original.store.repositories.entities.list_agents()[0]["id"]
                refs = lambda platform: {item["ref"] for item in platform.decision_view(actor_id)["executable_behaviors"]}
                assert refs(imported) == refs(original)
