from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..persistence.store import WorldStore


class PerceptionService:
    """Role-scoped, read-only information projection for Agent tools."""

    def __init__(self, store: "WorldStore"):
        self.store = store

    def _policy(self, entity_type: str, *, global_scope: bool = False) -> dict[str, Any]:
        """Return the declarative projection policy for one entity type.

        Entity records are Platform truth, not an observation payload.  A
        read tool therefore selects only configured public fields instead of
        copying an arbitrary database row.  Worlds may add entirely different
        entity properties without changing this code; unlisted properties stay
        private by default.
        """

        rules = self.store.get_meta("rules", {}) or {}
        root = rules.get("perception", {}) if isinstance(rules, dict) else {}
        root = root if isinstance(root, dict) else {}
        policy: dict[str, Any] = {
            "fields": list(root.get("visible_fields", ["role", "type", "summary", "description", "public"]) or []),
            "state_fields": list(root.get("visible_state_fields", ["status"]) or []),
        }
        typed = (root.get("types", {}) or {}).get(str(entity_type), {})
        if isinstance(typed, dict):
            if "fields" in typed:
                policy["fields"] = list(typed.get("fields") or [])
            if "state_fields" in typed:
                policy["state_fields"] = list(typed.get("state_fields") or [])
        if global_scope:
            global_policy = root.get("global", {}) or {}
            if isinstance(global_policy, dict):
                policy["fields"] = list(global_policy.get("fields", policy["fields"]) or [])
                policy["state_fields"] = list(global_policy.get("state_fields", policy["state_fields"]) or [])
        return policy

    def _project_entity(
        self,
        entity: dict[str, Any],
        *,
        include_state: bool,
        global_scope: bool = False,
    ) -> dict[str, Any]:
        entity_type = str(entity.get("entity_type") or entity.get("type") or "entity")
        policy = self._policy(entity_type, global_scope=global_scope)
        projected: dict[str, Any] = {
            "name": str(entity.get("name") or "unknown entity"),
            "type": entity_type,
        }
        for field in policy["fields"]:
            if field in {"id", "entity_type", "name", "initial_state"}:
                continue
            if entity.get(field) not in (None, "", [], {}):
                projected[str(field)] = entity[field]
        if include_state:
            state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
            visible_state = {
                str(field): state[field]
                for field in policy["state_fields"]
                if field != "location_id" and state.get(field) not in (None, "", [], {})
            }
            location_id = state.get("location_id")
            if "location_id" in policy["state_fields"] and location_id:
                try:
                    visible_state["location"] = self.store.get_entity(str(location_id)).get("name", location_id)
                except KeyError:
                    visible_state["location"] = "known location"
            if visible_state:
                projected["known_state"] = visible_state
        return projected

    def _observation_meta(self, *, visibility: str) -> dict[str, Any]:
        return {
            "observed_at": int(self.store.get_meta("world_minute", 0) or 0),
            "source": "platform_perception",
            "visibility": visibility,
            "confidence": 1.0,
            "expires_at": None,
        }

    def observe_nearby(self, agent_id: str, question: str = "") -> dict[str, Any]:
        try:
            agent = self.store.get_agent(agent_id)
            location_id = agent.get("initial_state", {}).get("location_id")
            raw_entities = [item for item in self.store.entities_at_location(location_id) if item["id"] != agent_id]
            entities = [self._project_entity(item, include_state=True) for item in raw_entities[:8]]
        except KeyError:
            location_id = "world"
            entities = []
            for entity_type in ("location", "faction"):
                entities.extend(
                    self._project_entity(item, include_state=False, global_scope=True)
                    for item in self.store.entities_of_type(entity_type)[:8]
                )
        try:
            location = self.store.get_entity(str(location_id)).get("name", location_id)
        except KeyError:
            location = location_id
        return {
            "status": "ok",
            "location": location,
            "question": question,
            "facts": [f"{item['name']} is here ({item.get('summary', item.get('type', 'entity'))})." for item in entities[:8]],
            "entities": entities[:8],
            **self._observation_meta(visibility="nearby"),
        }

    def inspect_entity(self, agent_id: str, entity_id: str, question: str = "", *, global_scope: bool = False) -> dict[str, Any]:
        entity = self.store.get_entity(entity_id)
        visibility = "global" if global_scope else "known"
        include_state = bool(global_scope)
        if not global_scope:
            agent = self.store.get_agent(agent_id)
            state = agent.get("initial_state", {})
            nearby_ids = {item["id"] for item in self.store.entities_at_location(state.get("location_id"))}
            seeded = {
                str(value.get("entity") or value.get("id") or value.get("fact_key") or "").split(":", 1)[-1]
                if isinstance(value, dict) else str(value).split(":", 1)[-1]
                for value in agent.get("knowledge_seed", [])
                if isinstance(value, (str, dict))
            }
            if entity_id != agent_id and entity_id not in nearby_ids and entity_id not in seeded:
                return {"status": "rejected", "failed_check": "visibility", "retryable": True, "message": "This entity is not visible or known to the agent."}
            include_state = entity_id == agent_id or entity_id in nearby_ids
            visibility = "self" if entity_id == agent_id else ("nearby" if entity_id in nearby_ids else "known")
        data = self._project_entity(entity, include_state=include_state, global_scope=global_scope)
        return {"status": "ok", "entity": data, "question": question, **self._observation_meta(visibility=visibility)}

    def listen(self, agent_id: str, question: str = "", limit: int = 5) -> dict[str, Any]:
        try:
            location_id = self.store.get_agent(agent_id).get("initial_state", {}).get("location_id")
        except KeyError:
            location_id = None
        rules = self.store.get_meta("rules", {}) or {}
        policy = (rules.get("perception", {}) or {}) if isinstance(rules, dict) else {}
        audible_types = {str(item) for item in (policy.get("audible_event_types", []) or [])}
        audible_prefixes = tuple(str(item) for item in (policy.get("audible_event_prefixes", []) or []))
        events = []
        for event in self.store.recent_events(limit=max(1, min(int(limit) * 4, 40))):
            event_type = str(event.get("event_type", ""))
            if (audible_types or audible_prefixes) and event_type not in audible_types and not event_type.startswith(audible_prefixes):
                continue
            event_location = event.get("location_id") or event.get("payload", {}).get("location_id") if isinstance(event.get("payload"), dict) else event.get("location_id")
            if event_location is None or location_id is None or event_location == location_id:
                if event_type.startswith("runtime.") or event_type == "llm.call":
                    continue
                events.append({"event_type": event_type, "world_minute": event.get("world_minute"), "summary": event.get("summary") or event.get("result") or event.get("topic") or event_type})
            if len(events) >= limit:
                break
        return {
            "status": "ok",
            "question": question,
            "events": events,
            **self._observation_meta(visibility="audible_local_events"),
        }

    def read_public_record(self, agent_id: str, entity_id: str, question: str = "", *, global_scope: bool = False) -> dict[str, Any]:
        entity = self.store.get_entity(entity_id)
        if not global_scope and not entity.get("public", False):
            return {"status": "rejected", "failed_check": "publicity", "retryable": True, "message": "This record is not public."}
        record = self._project_entity(entity, include_state=True, global_scope=global_scope)
        return {
            "status": "ok",
            "question": question,
            "record": record,
            **self._observation_meta(visibility="public_record"),
        }

    def query_known_fact(self, agent_id: str, query: str = "", limit: int = 8) -> dict[str, Any]:
        facts = self.store.list_knowledge_facts(agent_id, limit=50)
        if query.strip():
            query_lower = query.lower()
            facts = [fact for fact in facts if query_lower in str(fact).lower()]
        visible_fields = {"fact", "summary", "source_type", "source", "confidence", "claim_status", "observed_at", "expires_at"}
        visible_facts = [
            {key: value for key, value in fact.items() if key in visible_fields and value not in (None, "", [], {})}
            for fact in facts[: max(1, min(int(limit), 8))]
        ]
        return {
            "status": "ok",
            "query": query,
            "facts": visible_facts,
            **self._observation_meta(visibility="agent_knowledge"),
        }
