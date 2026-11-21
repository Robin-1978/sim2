"""Shared API error types."""

from __future__ import annotations


class NoActiveWorldError(RuntimeError):
    """Raised when a world endpoint is called before any instance is open."""
