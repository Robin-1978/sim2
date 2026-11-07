from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..persistence.store import WorldStore


class PerceptionService:
    """Role-scoped, read-only information projection for Agent tools."""

    def __init__(self, store: "WorldStore"):
        self.store = store

    def observe_nearby(self, agent_id: str, question: str = "") -> dict[str, Any]:
        try:
            agent = self.store.get_agent(agent_id)
            location_id = agent.get("initial_state", {}).get("location_id")
            entities = [item for item in self.store.entities_at_location(location_id) if item["id"] != agent_id]
        except KeyError:
            location_id = "world"
            entities = []
            for entity_type in ("location", "faction"):
                entities.extend(self.store.entities_of_type(entity_type)[:8])
        return {
            "status": "ok",
            "location": location_id,
            "question": question,
            "facts": [f"{item['name']} is here ({item.get('summary', item.get('type', 'entity'))})." for item in entities[:8]],
            "entities": entities[:8],
            "source": "platform_perception",
        }

    def inspect_entity(self, agent_id: str, entity_id: str, question: str = "", *, global_scope: bool = False) -> dict[str, Any]:
        entity = self.store.get_entity(entity_id)
        if not global_scope:
            agent = self.store.get_agent(agent_id)
            state = agent.get("initial_state", {})
            nearby_ids = {item["id"] for item in self.store.entities_at_location(state.get("location_id"))}
            seeded = {str(value).split(":", 1)[-1] for value in agent.get("knowledge_seed", []) if isinstance(value, str)}
            if entity_id != agent_id and entity_id not in nearby_ids and entity_id not in seeded:
                return {"status": "rejected", "failed_check": "visibility", "retryable": True, "message": "This entity is not visible or known to the agent."}
        private_fields = {"beliefs", "knowledge_seed", "inventory", "combat_stats", "initial_bt_ids", "external_pressure", "relation_pressure"}
        data = {key: value for key, value in entity.items() if key not in private_fields and not key.startswith("_")}
        return {"status": "ok", "entity": data, "question": question, "source": "platform_perception"}

    def listen(self, agent_id: str, question: str = "", limit: int = 5) -> dict[str, Any]:
        try:
            location_id = self.store.get_agent(agent_id).get("initial_state", {}).get("location_id")
        except KeyError:
            location_id = None
        events = []
        for event in self.store.recent_events(limit=max(1, min(int(limit) * 4, 40))):
            event_location = event.get("location_id") or event.get("payload", {}).get("location_id") if isinstance(event.get("payload"), dict) else event.get("location_id")
            if event_location is None or location_id is None or event_location == location_id:
                if event.get("event_type", "").startswith("runtime.") or event.get("event_type") == "llm.call":
                    continue
                events.append({"event_type": event.get("event_type"), "world_minute": event.get("world_minute"), "summary": event.get("result") or event.get("topic") or event.get("event_type")})
            if len(events) >= limit:
                break
        return {"status": "ok", "question": question, "events": events, "source": "audible_local_events"}

    def read_public_record(self, agent_id: str, entity_id: str, question: str = "", *, global_scope: bool = False) -> dict[str, Any]:
        entity = self.store.get_entity(entity_id)
        if not global_scope and not entity.get("public", False):
            return {"status": "rejected", "failed_check": "publicity", "retryable": True, "message": "This record is not public."}
        return {"status": "ok", "question": question, "record": {key: value for key, value in entity.items() if not key.startswith("_")}, "source": "public_record"}

    def query_known_fact(self, agent_id: str, query: str = "", limit: int = 8) -> dict[str, Any]:
        facts = self.store.list_knowledge_facts(agent_id, limit=50)
        if query.strip():
            query_lower = query.lower()
            facts = [fact for fact in facts if query_lower in str(fact).lower()]
        return {"status": "ok", "query": query, "facts": facts[: max(1, min(int(limit), 8))], "source": "agent_knowledge"}
