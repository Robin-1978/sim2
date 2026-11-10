from __future__ import annotations

"""Configuration-driven world rule compilation.

Rules are deliberately small declarative records.  They may gate a configured
action, expose a policy signal to an Agent, or emit a low-frequency world event
when their condition becomes true.  They never mutate world state themselves;
the configured behavior that follows the signal owns that mutation.
"""

import re
from typing import Any

from ..actions.conditions import ConditionEngine


class RuleCompiler:
    """Compile and evaluate rules imported into a world instance."""

    _DENY_EFFECTS = {"deny", "deny_access", "deny_action", "forbid", "behavior_constraint"}
    _SIGNAL_EFFECTS = {"signal", "event", "notify", "wake"}

    def __init__(
        self,
        rules: dict[str, Any] | None = None,
        conditions: ConditionEngine | None = None,
        aliases: dict[str, str] | None = None,
        known_behaviors: list[dict[str, Any]] | None = None,
        known_actions: list[dict[str, Any]] | None = None,
        world_keys: set[str] | None = None,
    ):
        self.rules = rules or {}
        self.conditions = conditions or ConditionEngine(self.rules)
        self.aliases = {str(key): str(value) for key, value in (aliases or {}).items() if str(key) and str(value)}
        self.known_behaviors = list(known_behaviors or [])
        self.known_actions = list(known_actions or [])
        self.world_keys = {str(item) for item in (world_keys or set())}

    def all_rules(self) -> list[dict[str, Any]]:
        values = self.rules.get("world_rules", []) if isinstance(self.rules, dict) else []
        return [item for item in values if isinstance(item, dict) and self._valid_shape(item)]

    def compile(self, rule: dict[str, Any]) -> dict[str, Any]:
        """Return a normalized rule without changing its configured meaning."""

        raw_effect = rule.get("effect", rule.get("enforcement"))
        if isinstance(raw_effect, str):
            effect_name = raw_effect.strip()
            effect = {"type": "deny" if effect_name.casefold() in self._DENY_EFFECTS else "signal", "name": effect_name}
        elif isinstance(raw_effect, dict):
            effect = dict(raw_effect)
            effect.setdefault("type", "signal")
        else:
            effect = {"type": "signal", "name": "rule_triggered"}
        condition = str(rule.get("condition", rule.get("when", ""))).strip()
        return {
            "name": str(rule.get("name") or rule.get("label") or rule.get("id") or "world rule"),
            "condition": condition,
            "description": str(rule.get("description") or rule.get("summary") or ""),
            "effect": effect,
            "scope": str(rule.get("scope") or "world"),
            "wake": [str(item) for item in (rule.get("wake") or effect.get("wake") or [])],
            "event_type": str(rule.get("event_type") or effect.get("event") or ""),
            "behavior_names": [str(item) for item in (rule.get("behavior_names") or effect.get("behavior_names") or [])],
            "behavior_ids": [str(item) for item in (rule.get("behavior_ids") or effect.get("behavior_ids") or [])],
            "action_names": [str(item) for item in (rule.get("action_names") or effect.get("action_names") or [])],
            "raw": dict(rule),
        }

    @staticmethod
    def _valid_shape(rule: dict[str, Any]) -> bool:
        return bool(str(rule.get("id") or rule.get("name") or "").strip()) and bool(str(rule.get("condition", rule.get("when", ""))).strip()) and bool(rule.get("effect", rule.get("enforcement")))

    def validate(self, rule: dict[str, Any]) -> tuple[bool, str]:
        if not isinstance(rule, dict) or not str(rule.get("id") or rule.get("name") or "").strip():
            return False, "A rule needs a non-empty id or name."
        condition = str(rule.get("condition", rule.get("when", ""))).strip()
        if not condition:
            return False, "A rule needs a declarative condition."
        supported, message = self._condition_supported(condition)
        if not supported:
            return False, message
        effect = rule.get("effect", rule.get("enforcement"))
        if not isinstance(effect, (str, dict)) or (isinstance(effect, str) and not effect.strip()):
            return False, "A rule needs a string or object effect."
        normalized = self.compile(rule)
        behavior_selectors = normalized["behavior_ids"] + normalized["behavior_names"]
        action_selectors = list(rule.get("action_names") or normalized["effect"].get("action_names") or [])
        if str(normalized["effect"].get("type", "")).casefold() == "behavior_constraint" and len(behavior_selectors) > 1:
            return False, "A behavior_constraint blocks every listed behavior; list only the behavior that must be blocked."
        raw_effect = rule.get("effect", rule.get("enforcement"))
        if isinstance(raw_effect, str) and (behavior_selectors or action_selectors) and not re.fullmatch(r"[A-Za-z0-9_.-]+", raw_effect.strip()):
            return False, "A rule with a behavior follow-up needs one supported effect name without spaces (for example policy.review_signal) or a typed effect object such as {type: signal, message: ...}."
        if normalized["effect"].get("type") not in self._DENY_EFFECTS | self._SIGNAL_EFFECTS | {"policy", "behavior_constraint"}:
            return False, "Rule effect type is not supported."
        unknown_behaviors = [value for value in behavior_selectors if not self._known_behavior(value)]
        if unknown_behaviors:
            return False, f"Unknown behavior follow-up: {', '.join(unknown_behaviors)}."
        unknown_actions = [value for value in action_selectors if not self._known_action(value)]
        if unknown_actions:
            return False, f"Unknown action follow-up: {', '.join(unknown_actions)}."
        return True, ""

    def validate_declared_effects(self, rule: dict[str, Any], statements: list[Any] | None = None) -> tuple[bool, str]:
        """Validate declared enforcement from the typed effect object.

        Natural-language descriptions are evidence for an Agent, not an
        execution contract.  Inferring enforcement from words such as
        ``gate`` or ``must`` causes false positives for ordinary names and
        translations.  The effect type is the only enforcement source of
        truth; Platform never lets a signal mutate or block state.
        """

        del statements
        raw_effect = rule.get("effect", rule.get("enforcement"))
        if not isinstance(raw_effect, dict):
            return True, ""
        effect_type = str(raw_effect.get("type") or "signal").casefold()
        declared = str(raw_effect.get("enforcement") or "").casefold()
        if declared and declared not in {"none", "signal", effect_type}:
            return False, f"Typed effect enforcement '{declared}' does not match effect type '{effect_type}'. Use a matching denial or behavior_constraint effect."
        return True, ""

    def validate_declared_conditions(self, rule: dict[str, Any], declared: list[Any] | None = None) -> tuple[bool, str]:
        """Ensure explicit machine conditions are represented by the rule."""

        machine_condition = self._normalize_expression(str(rule.get("condition", rule.get("when", ""))))
        for item in declared or []:
            text = self._normalize_expression(str(item or ""))
            paths = self._condition_paths(text)
            missing = [path for path in paths if path not in machine_condition]
            if missing:
                return False, f"Declared condition fields are absent from the rule condition: {', '.join(missing)}."
        return True, ""

    def signal_conflict(self, rule: dict[str, Any], existing_rules: list[dict[str, Any]] | None = None) -> tuple[str, str] | None:
        """Find generic signal duplication or same-role signal chaining.

        A signal rule is a notification boundary, not a stream processor.  If
        God registers another signal for the same validated event and the same
        follow-up, or wires one God-woken signal into another God-woken signal,
        the proposal would create an endless policy chain instead of world
        progress.  The check uses only declarative event paths, wake roles and
        selectors; it contains no package-specific event or behavior names.
        """

        compiled = self.compile(rule)
        effect_type = str(compiled["effect"].get("type", "")).casefold()
        if effect_type not in self._SIGNAL_EFFECTS | {"policy"}:
            return None
        event_paths = set(self._event_paths(compiled["condition"]))
        selectors = {self._normal(item) for item in (compiled["behavior_ids"] + compiled["behavior_names"] + compiled["action_names"]) if self._normal(item)}
        wake = {self._normal(item) for item in compiled["wake"] if self._normal(item)}
        effect_name = self._normal(compiled["effect"].get("name") or compiled["effect"].get("event") or compiled["effect"].get("type"))
        for raw in existing_rules or []:
            if not isinstance(raw, dict):
                continue
            other = self.compile(raw)
            other_type = str(other["effect"].get("type", "")).casefold()
            if other_type not in self._SIGNAL_EFFECTS | {"policy"}:
                continue
            other_paths = set(self._event_paths(other["condition"]))
            other_selectors = {self._normal(item) for item in (other["behavior_ids"] + other["behavior_names"] + other["action_names"]) if self._normal(item)}
            other_wake = {self._normal(item) for item in other["wake"] if self._normal(item)}
            other_effect_name = self._normal(other["effect"].get("name") or other["effect"].get("event") or other["effect"].get("type"))
            if event_paths and event_paths & other_paths and selectors == other_selectors and wake == other_wake and effect_name == other_effect_name:
                return "duplicate", str(other.get("name") or raw.get("id") or "existing signal")
            source_ids = {path.removeprefix("event.recent.world.rule.") for path in event_paths if path.startswith("event.recent.world.rule.")}
            existing_id = str(raw.get("id") or raw.get("name") or "")
            if source_ids and self._normal(existing_id) in {self._normal(item) for item in source_ids} and wake & other_wake:
                return "cycle", str(other.get("name") or existing_id or "existing signal")
        return None

    @staticmethod
    def _condition_paths(expression: str) -> list[str]:
        paths = re.findall(r"(?<![A-Za-z0-9_])((?:world|resource|actor|target|context|event\.recent)(?:\.[A-Za-z0-9_]+)+)", expression)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _event_paths(expression: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"(?<![A-Za-z0-9_])(event\.recent(?:\.[A-Za-z0-9_]+)+)", str(expression or "").casefold())))

    def _condition_supported(self, expression: str) -> tuple[bool, str]:
        """Reject structured paths that the condition engine cannot observe."""

        expression = self._normalize_expression(expression)
        paths = re.findall(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_.]*)\s*(?:==|!=|>=|<=|>|<|\s+in\s+|\s+not_in\s+)", expression)
        for path in paths:
            if path.startswith("world.") or path.startswith("resource."):
                key = path.split(".", 1)[1]
                if self.world_keys and key not in self.world_keys:
                    return False, f"Unknown world property in rule condition: {key}."
            elif path.startswith("actor.") or path.startswith("target.") or path.startswith("context.") or path.startswith("event.recent."):
                continue
            elif "." in path and not path.startswith("world."):
                # A namespaced path outside the generic Agent/target/context
                # projections is not evaluable by Platform.
                return False, f"Unsupported rule condition path: {path}."
        return True, ""

    def _normalize_expression(self, expression: str) -> str:
        expression = str(expression or "")
        for match in re.findall(r"world\[(?:\"([^\"]+)\"|'([^']+)')\]", expression):
            visible = match[0] or match[1]
            identifier = self.aliases.get(visible)
            if identifier:
                expression = expression.replace(f'world["{visible}"]', f"world.{identifier}").replace(f"world['{visible}']", f"world.{identifier}")
        for visible, identifier in sorted(self.aliases.items(), key=lambda pair: len(pair[0]), reverse=True):
            expression = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(visible)}(?![A-Za-z0-9_])", identifier, expression)
        return expression

    def _known_behavior(self, value: Any) -> bool:
        key = self._normal(value)
        return any(key in {
            self._normal(item.get("id")), self._normal(item.get("agent_label")),
            self._normal(item.get("summary")), self._normal(item.get("description")),
        } for item in self.known_behaviors)

    def _known_action(self, value: Any) -> bool:
        key = self._normal(value)
        return any(key in {
            self._normal(item.get("id")), self._normal(item.get("agent_label")),
            self._normal(item.get("agent_summary")), self._normal(item.get("description")),
        } for item in self.known_actions)

    def matches(self, rule: dict[str, Any], *, agent: dict[str, Any], target: dict[str, Any] | None, context: dict[str, Any] | None, world: dict[str, Any] | None) -> bool:
        compiled = self.compile(rule)
        expression = compiled["condition"]
        if not expression:
            return False
        # Resource paths are a readable alias for the world projection.  The
        # condition engine remains generic and the rule stays package-owned.
        expression = expression.replace("resource.", "world.")
        expression = self._normalize_expression(expression)
        return bool(self.conditions.evaluate(expression, agent=agent, target=target or {}, context=context or {}, world=world or {}))

    def denial(self, *, action: dict[str, Any], agent: dict[str, Any], target: dict[str, Any] | None, context: dict[str, Any] | None, world: dict[str, Any] | None) -> dict[str, Any] | None:
        for raw in self.all_rules():
            rule = self.compile(raw)
            effect_type = str(rule["effect"].get("type", "")).casefold()
            if effect_type not in self._DENY_EFFECTS or not self.matches(raw, agent=agent, target=target, context=context, world=world):
                continue
            if not self._targets_action(rule, action):
                continue
            return {"rule": rule["name"], "message": str(rule["effect"].get("message") or rule["description"] or f"Action blocked by {rule['name']}.")}
        return None

    def behavior_denial(self, *, behavior: dict[str, Any], agent: dict[str, Any], context: dict[str, Any] | None, world: dict[str, Any] | None) -> dict[str, Any] | None:
        """Apply a typed behavior constraint before a BT can be selected."""

        for raw in self.all_rules():
            rule = self.compile(raw)
            effect_type = str(rule["effect"].get("type", "")).casefold()
            if effect_type not in self._DENY_EFFECTS or not self.behavior_matches(raw, behavior):
                continue
            if not self.matches(raw, agent=agent, target={}, context=context, world=world):
                continue
            return {"rule": rule["name"], "message": str(rule["effect"].get("message") or rule["description"] or f"Behavior blocked by {rule['name']}.")}
        return None

    def _targets_action(self, rule: dict[str, Any], action: dict[str, Any]) -> bool:
        effect = rule["effect"]
        action_id = str(action.get("id") or "")
        action_candidates = {self._normal(action_id), self._normal(action.get("agent_label")), self._normal(action.get("agent_summary")), self._normal(action.get("description"))}
        action_names = {self._normal(item) for item in rule.get("action_names", [])}
        configured_actions = effect.get("action_ids") or effect.get("actions") or []
        if isinstance(configured_actions, str):
            configured_actions = [configured_actions]
        if configured_actions:
            return any(self._normal(item) in action_candidates for item in configured_actions) or bool(action_candidates & action_names)
        if action_names:
            return bool(action_candidates & action_names)
        # A behavior-only constraint is evaluated by ``behavior_denial``.
        # Treating it as a global Action denial would hide unrelated actions
        # such as eating, resting, or gathering whenever one BT is held.
        if rule.get("behavior_ids") or rule.get("behavior_names"):
            return False
        # A rule without an action selector is intentionally global.  Packages
        # should add action_names/action_ids when a denial is location-specific.
        return True

    def model_view(self, labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Return concise policy text; internal ids stay out of the prompt."""

        labels = {str(key): str(value) for key, value in (labels or {}).items()}
        result = []
        for raw in self.all_rules():
            rule = self.compile(raw)
            effect = rule["effect"]
            effect_text = str(effect.get("name") or effect.get("type") or "configured policy").replace("_", " ")
            condition = rule["condition"]
            for key, label in sorted(labels.items(), key=lambda pair: len(pair[0]), reverse=True):
                condition = condition.replace(f"resource.{key}", label).replace(f"world.{key}", label)
                condition = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", label, condition)
            display_name = str(rule["raw"].get("label") or rule["raw"].get("name") or rule["description"] or "world policy")
            item = {"name": display_name, "condition": condition, "effect": effect_text}
            if rule["description"] and display_name != rule["description"]:
                item["description"] = rule["description"]
            if rule["behavior_names"]:
                item["applies_to"] = list(rule["behavior_names"])
            result.append(item)
        return result[:16]

    @staticmethod
    def _normal(value: Any) -> str:
        return "".join(char for char in str(value or "").casefold() if char.isalnum())

    def behavior_matches(self, rule: dict[str, Any], behavior: dict[str, Any]) -> bool:
        """Return whether a rule explicitly names the behavior it governs."""

        compiled = self.compile(rule)
        selectors = list(compiled["behavior_ids"]) + list(compiled["behavior_names"])
        if not selectors:
            return False
        candidates = {
            self._normal(behavior.get("id")),
            self._normal(behavior.get("agent_label")),
            self._normal(behavior.get("summary")),
            self._normal(behavior.get("description")),
        }
        return any(self._normal(selector) in candidates for selector in selectors)

    def signal_specs(self, *, agent: dict[str, Any], context: dict[str, Any] | None, world: dict[str, Any] | None) -> list[dict[str, Any]]:
        result = []
        for raw in self.all_rules():
            rule = self.compile(raw)
            effect_type = str(rule["effect"].get("type", "")).casefold()
            if effect_type not in self._SIGNAL_EFFECTS | {"policy", "behavior_constraint"}:
                continue
            if not self.matches(raw, agent=agent, target={}, context=context, world=world):
                continue
            stable_id = str(raw.get("id") or raw.get("name") or rule["name"])
            event_type = rule["event_type"] or f"world.rule.{stable_id}"
            result.append({"id": stable_id, "name": rule["name"], "event_type": event_type, "wake": rule["wake"], "effect": rule["effect"], "condition": rule["condition"]})
        return result
