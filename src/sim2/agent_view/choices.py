"""Stable visible choices for behaviors and trade offers, and their resolution back to references."""

from __future__ import annotations

from typing import Any

from .action_text import choice_key, label


class ChoiceMixin:
    """Methods of ``AgentViewCompiler`` for this concern; they use the attributes its constructor creates."""

    @staticmethod
    def _assign_choices(behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Give the current view short, human-readable, collision-free choices.

        ``ref`` remains an internal Platform value.  The choice is the only
        behavior selector rendered to the model.  A suffix is used only when
        two configured behaviors have the same visible name.
        """

        counts: dict[str, int] = {}
        for behavior in behaviors:
            base = str(behavior.get("choice") or behavior.get("name") or "Choose")
            key = choice_key(base) or "choice"
            counts[key] = counts.get(key, 0) + 1
        seen: dict[str, int] = {}
        result: list[dict[str, Any]] = []
        for behavior in behaviors:
            item = dict(behavior)
            base = str(item.get("choice") or item.get("name") or "Choose")
            key = choice_key(base) or "choice"
            seen[key] = seen.get(key, 0) + 1
            item["choice"] = base if counts[key] == 1 else f"{base} (option {seen[key]})"
            result.append(item)
        return result

    def option_snapshot(self, agent_id: str, *, bt_defs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Project one character's executable options for stagnation audits.

        The character's own decision view is the only authority on what it can
        do now, so the snapshot is derived from it instead of re-implementing
        executability.  ``options`` are stable identifiers: a behavior, or a
        behavior plus one parameter option (for example a configured research
        source), so new content that only adds a parameter option still counts
        as growth.  ``structure`` excludes parameter-only additions: it holds
        behaviors plus reachable places, factions and rules.  ``choices`` and
        ``blocked`` use the visible wording the character sees; blocked entries
        carry the failing conditions.
        """

        view = self.build_decision_view(agent_id, role="agent_decision", include_diagnostics=True)
        definitions = bt_defs if bt_defs is not None else {str(item["id"]): item for item in self._definitions("bt_def")}
        options: set[str] = set()
        structure: set[str] = set()
        choices: list[str] = []
        for item in view.get("executable_behaviors", []) or []:
            ref = str(item.get("ref") or "")
            if not ref:
                continue
            options.add(ref)
            structure.add(ref)
            choices.append(str(item.get("choice") or ref))
            for parameter, entries in (item.get("parameter_options") or {}).items():
                for entry in entries or []:
                    name = str((entry or {}).get("name") or "").strip()
                    if name:
                        options.add(f"{ref}|{parameter}={name}")
        self_view = view.get("self") if isinstance(view.get("self"), dict) else {}
        for route in self_view.get("known_routes") or []:
            if str(route).strip():
                structure.add("location:" + " ".join(str(route).casefold().split()))
        for faction in self_view.get("faction_ids") or []:
            if str(faction).strip():
                structure.add("faction:" + str(faction))
        for rule in view.get("active_world_rules") or []:
            rule_label = str((rule or {}).get("name") or (rule or {}).get("id") or "").strip()
            if rule_label:
                structure.add("rule:" + " ".join(rule_label.casefold().split()))
        blocked: list[dict[str, Any]] = []
        for diagnostic in view.get("unavailable_behavior_diagnostics", []) or []:
            reasons = [str(text) for text in (diagnostic.get("failed") or [])]
            # A cooldown is a temporary pause, not a structural obstacle.
            if reasons and all(text.startswith("cooldown until") for text in reasons):
                continue
            behavior_id = str(diagnostic.get("behavior_id") or "")
            definition = definitions.get(behavior_id, {})
            blocked.append({
                "choice": str(definition.get("agent_label") or definition.get("summary") or label(behavior_id)),
                "needs": [str(text)[:120] for text in (diagnostic.get("failed") or [])][:3],
            })
        return {"options": sorted(options), "structure": sorted(structure), "choices": choices, "blocked": blocked}

    def resolve_behavior_choice(self, agent_id: str, choice: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve a visible choice to an internal behavior only in Platform.

        Matching is exact after case/spacing/punctuation normalization.  No
        fuzzy matching is used because a wrong behavior can have side effects.
        ``behavior:<id>`` is accepted only as a private compatibility path for
        old integrations; it is never advertised in the model context.
        """

        value = str(choice or "").strip()
        view = self.build_decision_view(agent_id, role="agent_decision")
        behaviors = view.get("executable_behaviors", [])
        if value.startswith("behavior:"):
            matches = [item for item in behaviors if item.get("ref") == value]
        else:
            key = choice_key(value)
            matches = [item for item in behaviors if choice_key(item.get("choice") or item.get("name")) == key]
        if len(matches) == 1:
            return matches[0], None
        available = [str(item.get("choice") or item.get("name")) for item in behaviors]
        if not matches:
            return None, {
                "status": "rejected",
                "failed_check": "behavior_choice",
                "retryable": True,
                "message": "Choose one behavior by its exact visible name.",
                "available_choices": available,
            }
        return None, {
            "status": "rejected",
            "failed_check": "ambiguous_behavior_choice",
            "retryable": True,
            "message": "That behavior name is ambiguous; choose the displayed option.",
            "available_choices": available,
        }

    def _trade_offer_view(self, agent_id: str) -> list[dict[str, Any]]:
        offers: list[dict[str, Any]] = []
        for offer in self.social_repo.list_trade_offers(agent_id, include_closed=False, limit=8):
            other_id = offer["recipient_id"] if offer["offerer_id"] == agent_id else offer["offerer_id"]
            try:
                other_name = self.entities_repo.get_agent(other_id).get("name", other_id)
            except KeyError:
                other_name = other_id
            direction = "to" if offer["offerer_id"] == agent_id else "from"
            give = ", ".join(f"{amount} {item}" for item, amount in offer.get("give", {}).items())
            want = ", ".join(f"{amount} {item}" for item, amount in offer.get("want", {}).items())
            offers.append({
                "offer_id": offer["offer_id"],
                "choice": f"Offer {direction} {other_name}: give {give}; want {want}",
                "summary": f"Trade {give} for {want}.",
                "round": offer.get("round", 1),
                "status": offer.get("status", "open"),
            })
        return offers

    def resolve_trade_offer_choice(self, agent_id: str, choice: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        value = str(choice or "").strip()
        offers = self._trade_offer_view(agent_id)
        if value in {str(item.get("offer_id")) for item in offers}:
            matches = [item for item in offers if str(item.get("offer_id")) == value]
        else:
            key = choice_key(value)
            matches = [item for item in offers if choice_key(item.get("choice")) == key]
        available = [str(item.get("choice")) for item in offers]
        if len(matches) == 1:
            return matches[0], None
        return None, {
            "status": "rejected",
            "failed_check": "trade_offer_choice",
            "retryable": True,
            "message": "Choose one exact open trade offer from the current interaction list.",
            "available_choices": available,
        }
