"""Resolve authored world property references for reads and effects."""

from __future__ import annotations

from typing import Any


def world_property_key(store: Any, ref: str) -> str:
    labels = (store.repositories.metadata.get("rules", {}) or {}).get("world_property_labels", {})
    if ref in labels:
        return ref
    identifier = "_".join(ref.casefold().split())
    if identifier in labels:
        return identifier
    matches = [key for key, spec in labels.items() if isinstance(spec, dict) and spec.get("label") == ref]
    return matches[0] if len(matches) == 1 else ref
