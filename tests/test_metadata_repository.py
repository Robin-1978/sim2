from __future__ import annotations

from sim2.persistence.store import WorldStore


def test_metadata_repository_owns_instance_metadata(tmp_path):
    store = WorldStore(tmp_path / "world.sqlite3")
    try:
        assert not hasattr(store, "get_meta")
        assert not hasattr(store, "set_meta")
        assert store.repositories.metadata.world_id() == ""
        assert store.repositories.metadata.world_minute() == 0

        with store.transaction():
            store.repositories.metadata.set("world_id", "test-world")
            store.repositories.metadata.set("world_minute", "12")
            store.repositories.metadata.set("rules", {"needs": [{"id": "focus"}]})

        assert store.repositories.metadata.world_id() == "test-world"
        assert store.repositories.metadata.world_minute() == 12
        assert store.repositories.metadata.rules() == {"needs": [{"id": "focus"}]}
    finally:
        store.close()


def test_metadata_write_rolls_back_with_the_unit_of_work(tmp_path):
    store = WorldStore(tmp_path / "world.sqlite3")
    try:
        try:
            with store.transaction():
                store.repositories.metadata.set("world_minute", 99)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        assert store.repositories.metadata.world_minute() == 0
    finally:
        store.close()

