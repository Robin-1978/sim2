from __future__ import annotations

import pytest

from worlds import world_path


def test_bundled_starter_village_is_an_importable_world_package():
    path = world_path("starter_village")

    assert (path / "manifest.yaml").is_file()
    assert (path / "actions.yaml").is_file()
    assert (path / "prompts" / "agent.yaml").is_file()


@pytest.mark.parametrize("world_id", ["", "../starter_village", "Starter-Village"])
def test_bundled_world_lookup_rejects_invalid_ids(world_id: str):
    with pytest.raises(ValueError, match="invalid bundled world id"):
        world_path(world_id)
