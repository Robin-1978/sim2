from __future__ import annotations


class _UnreachableProposal(ValueError):
    """Roll a proposal back because its content cannot be used."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class _ResidentAdmissionError(ValueError):
    """The settlement lacks an authorized intake point or real provisions."""
