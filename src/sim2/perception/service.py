from __future__ import annotations

import json
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
                    visible_state["location"] = self.store.repositories.entities.get_entity(str(location_id)).get("name", location_id)
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
            agent = self.store.repositories.entities.get_agent(agent_id)
            location_id = agent.get("initial_state", {}).get("location_id")
            raw_entities = [item for item in self.store.repositories.entities.entities_at_location(location_id) if item["id"] != agent_id]
            entities = [self._project_entity(item, include_state=True) for item in raw_entities[:8]]
        except KeyError:
            location_id = "world"
            entities = []
            for entity_type in ("location", "faction"):
                entities.extend(
                    self._project_entity(item, include_state=False, global_scope=True)
                    for item in self.store.repositories.entities.entities_of_type(entity_type)[:8]
                )
        try:
            location = self.store.repositories.entities.get_entity(str(location_id)).get("name", location_id)
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
        entity = self.store.repositories.entities.get_entity(entity_id)
        visibility = "global" if global_scope else "known"
        include_state = bool(global_scope)
        if not global_scope:
            agent = self.store.repositories.entities.get_agent(agent_id)
            state = agent.get("initial_state", {})
            nearby_ids = {item["id"] for item in self.store.repositories.entities.entities_at_location(state.get("location_id"))}
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
            location_id = self.store.repositories.entities.get_agent(agent_id).get("initial_state", {}).get("location_id")
        except KeyError:
            location_id = None
        rules = self.store.get_meta("rules", {}) or {}
        policy = (rules.get("perception", {}) or {}) if isinstance(rules, dict) else {}
        audible_types = {str(item) for item in (policy.get("audible_event_types", []) or [])}
        audible_prefixes = tuple(str(item) for item in (policy.get("audible_event_prefixes", []) or []))
        events = []
        for event in self.store.repositories.events.recent_events(limit=max(1, min(int(limit) * 4, 40))):
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
        entity = self.store.repositories.entities.get_entity(entity_id)
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
        facts = self.store.repositories.memory.list_knowledge_facts(agent_id, limit=50)
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

    def query_history(
        self,
        agent_id: str,
        *,
        event_type: str = "",
        actor: str = "",
        contains: str = "",
        since_minute: int | None = None,
        until_minute: int | None = None,
        limit: int = 8,
        global_scope: bool = False,
    ) -> dict[str, Any]:
        """Search validated event history without exposing implementation IDs.

        Story and God receive a bounded recent history in their normal view,
        then use this read tool for an older causal slice.  The query is
        deliberately declarative: it cannot mutate state and returns only
        visible event fields plus a compact, ID-free detail projection.
        """

        if not global_scope:
            return {
                "status": "rejected",
                "failed_check": "history_scope",
                "retryable": False,
                "message": "Only world-level Story and God sessions may query global history.",
            }
        event_filter = str(event_type or "").strip()
        actor_filter = str(actor or "").strip()
        contains_filter = str(contains or "").strip().casefold()
        low = max(0, int(since_minute)) if since_minute is not None else None
        high = max(0, int(until_minute)) if until_minute is not None else None
        if low is not None and high is not None and high < low:
            return {"status": "rejected", "failed_check": "history_range", "retryable": True, "message": "until_minute must be greater than or equal to since_minute."}
        max_rows = max(1, min(int(limit), 12))
        clauses = []
        params: list[Any] = []
        if event_filter:
            clauses.append("event_type=?")
            params.append(event_filter)
        if low is not None:
            clauses.append("world_minute>=?")
            params.append(low)
        if high is not None:
            clauses.append("world_minute<=?")
            params.append(high)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.store.connection.execute(
            "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log"
            + where + " ORDER BY event_id DESC LIMIT 5000",
            tuple(params),
        ).fetchall()
        actor_names: dict[str, str] = {}
        for entity_type in ("agent", "location", "object", "faction", "resource", "material"):
            for entity in self.store.repositories.entities.entities_of_type(entity_type):
                actor_names[str(entity.get("id"))] = str(entity.get("name") or entity.get("id"))
        actor_key = "".join(actor_filter.casefold().split())

        def compact(value: Any, depth: int = 0) -> Any:
            if depth > 2:
                return "…"
            if isinstance(value, dict):
                hidden = {"id", "event_id", "actor_id", "entity_id", "intent_id", "execution_id", "correlation_id", "causation_id"}
                return {
                    str(key): compact(item, depth + 1)
                    for key, item in value.items()
                    if str(key) not in hidden and not str(key).endswith("_id")
                }
            if isinstance(value, list):
                return [compact(item, depth + 1) for item in value[:8]]
            return value

        history: list[dict[str, Any]] = []
        for row in rows:
            actor_id = str(row["actor_id"] or "")
            actor_name = actor_names.get(actor_id, actor_id or "world")
            if actor_key and actor_key not in {"".join(actor_name.casefold().split()), "".join(actor_id.casefold().split())}:
                continue
            try:
                data = json.loads(row["data_json"] or "{}")
            except (TypeError, ValueError):
                data = {}
            searchable = " ".join([str(row["event_type"]), actor_name, json.dumps(data, ensure_ascii=False)]).casefold()
            if contains_filter and contains_filter not in searchable:
                continue
            history.append({
                "event_type": str(row["event_type"]),
                "world_minute": int(row["world_minute"]),
                "actor": actor_name,
                "details": compact(data),
            })
            if len(history) >= max_rows:
                break
        return {
            "status": "ok",
            "query": {
                "event_type": event_filter or "any",
                "actor": actor_filter or "any",
                "contains": contains or "any",
                "since_minute": low,
                "until_minute": high,
            },
            "history": history,
            "matched": len(history),
            **self._observation_meta(visibility="global_history"),
        }

    def query_knowledge_gaps(
        self,
        agent_id: str,
        *,
        status: str = "active",
        contains: str = "",
        limit: int = 8,
        global_scope: bool = False,
    ) -> dict[str, Any]:
        """List the world's current open or resolved knowledge questions.

        The event log records what happened.  This projection answers the
        smaller question world agents ask before proposing research: which
        questions remain open, and which one already has an answer.
        """

        if not global_scope:
            return {
                "status": "rejected",
                "failed_check": "knowledge_gap_scope",
                "retryable": False,
                "message": "Only world-level Story and God sessions may query knowledge gaps.",
            }
        requested = str(status or "active").strip().casefold()
        if requested not in {"active", "resolved", "all"}:
            return {
                "status": "rejected",
                "failed_check": "knowledge_gap_status",
                "retryable": True,
                "message": "status must be active, resolved or all.",
            }
        rows = self.store.repositories.memory.list_knowledge_gaps(limit=200)
        needle = str(contains or "").strip().casefold()
        source_names: dict[str, str] = {}
        for source in self.store.repositories.memory.research_source_catalog():
            source_names[str(source["id"])] = str(source.get("name") or source["id"])
        gaps: list[dict[str, Any]] = []
        for row in rows:
            if requested != "all" and str(row.get("status") or "").casefold() != requested:
                continue
            source_id = str(row.get("current_source_id") or "")
            source_name = source_names.get(source_id, "")
            searchable = " ".join([str(row.get("question") or ""), str(row.get("topic_key") or ""), source_name]).casefold()
            if needle and needle not in searchable:
                continue
            item: dict[str, Any] = {
                "question": str(row.get("question") or ""),
                "status": str(row.get("status") or "active"),
                "updated_minute": int(row.get("updated_minute") or 0),
            }
            if source_name:
                item["research_source"] = source_name
            total = int(row.get("total_stages") or 0)
            if total:
                item["stages"] = f"{len(row.get('completed_stages') or [])}/{total} completed"
            if row.get("last_fact"):
                item["last_finding"] = str(row.get("last_fact"))[:240]
            gaps.append(item)
            if len(gaps) >= max(1, min(int(limit), 12)):
                break
        return {
            "status": "ok",
            "filter": {"status": requested, "contains": contains or "any"},
            "gaps": gaps,
            "matched": len(gaps),
            **self._observation_meta(visibility="world_knowledge_gaps"),
        }
