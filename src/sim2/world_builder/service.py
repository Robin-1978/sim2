from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from ..agents.tools import parse_tool_call, tool_schemas_for_role
from ..bootstrap.service import create_instance, validate_world_template, write_world_package_from_template
from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform


@dataclass(frozen=True)
class WorldBuildDraft:
    template: dict[str, Any]
    iterations: int
    transcript: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class WorldBuilderError(RuntimeError):
    def __init__(self, message: str, *, transcript: list[dict[str, Any]], usage: dict[str, int]):
        super().__init__(message)
        self.transcript = list(transcript)
        self.usage = dict(usage)


class WorldBuilderService:
    """Creation-time bounded tool loop for a new declarative world package."""

    def __init__(self, provider: Any, *, max_iterations: int | None = None):
        self.provider = provider
        configured = getattr(provider, "max_agent_iterations", 8)
        self.max_iterations = max(2, int(configured if max_iterations is None else max_iterations))

    @staticmethod
    def _policy() -> tuple[str, list[str]]:
        resource = files("sim2.world_builder").joinpath("prompt.yaml")
        data = yaml.safe_load(resource.read_text(encoding="utf-8")) or {}
        return str(data.get("system") or ""), [str(item) for item in (data.get("requirements") or [])]

    @staticmethod
    def _digest(template: dict[str, Any]) -> str:
        encoded = json.dumps(template, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def build(self, brief: str) -> WorldBuildDraft:
        brief = str(brief or "").strip()
        if not brief:
            raise ValueError("A non-empty world brief is required.")
        system, requirements = self._policy()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": "World brief:\n" + brief + "\n\nCreation requirements:\n" + "\n".join(f"- {item}" for item in requirements),
            },
        ]
        tools = tool_schemas_for_role("world_builder")
        transcript: list[dict[str, Any]] = []
        validated_digest = ""
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}

        for iteration in range(1, self.max_iterations + 1):
            response = self.provider.complete_json(
                system=system,
                context=messages[-1].get("content", ""),
                tools=tools,
                messages=messages,
            )
            response = dict(response)
            usage = dict(response.pop("_usage", {}) or {})
            for key in usage_total:
                usage_total[key] += int(usage.get(key, 0) or 0)
            transcript.append({"step": "model", "response": self._readable_response(response), "usage": usage})
            call = parse_tool_call(response)
            if call is None:
                result = {"status": "rejected", "failed_check": "tool_required", "message": "Call validate_world_template, then submit_world_template."}
                self._append_result(messages, response, result)
                transcript.append({"step": "tool", "result": result})
                continue
            name, arguments = call
            template = arguments.get("template") if isinstance(arguments, dict) else None
            if not isinstance(template, dict):
                result = {"status": "rejected", "failed_check": "template", "message": "The tool requires one complete template object."}
            elif name == "validate_world_template":
                report = validate_world_template(template)
                runtime_errors = smoke_test_world_template(template) if report["valid"] else []
                errors = list(report["errors"]) + runtime_errors
                result = {
                    "status": "ok",
                    "valid": not errors,
                    "errors": errors[:40],
                    "message": "Template is valid and its initial behaviors completed in a temporary SQLite instance; submit this exact template." if not errors else "Correct every error and validate the complete revised template again.",
                }
                validated_digest = self._digest(template) if not errors else ""
            elif name == "submit_world_template":
                report = validate_world_template(template)
                digest = self._digest(template)
                if not report["valid"]:
                    result = {"status": "rejected", "valid": False, "errors": report["errors"][:40], "message": "The submitted template is invalid; correct and validate it."}
                elif digest != validated_digest:
                    result = {"status": "rejected", "failed_check": "not_exact_validated_template", "message": "Validate this exact template before submitting it."}
                else:
                    transcript.append({"step": "tool", "name": name, "result": {"status": "accepted", "valid": True}})
                    return WorldBuildDraft(template=template, iterations=iteration, transcript=transcript, usage=usage_total)
            else:
                result = {"status": "rejected", "failed_check": "tool_name", "message": "WorldBuilder may only validate or submit a world template."}
            transcript.append({"step": "tool", "name": name, "result": result})
            self._append_result(messages, response, result)
        raise WorldBuilderError(
            "WorldBuilder reached its iteration limit without an exact validated template.",
            transcript=transcript,
            usage=usage_total,
        )

    @staticmethod
    def _append_result(messages: list[dict[str, Any]], response: dict[str, Any], result: dict[str, Any]) -> None:
        native = response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else None
        if native:
            calls = []
            for index, item in enumerate(native):
                value = dict(item)
                value.setdefault("id", f"world-builder-{len(messages)}-{index}")
                calls.append(value)
            messages.append({"role": "assistant", "tool_calls": calls})
            messages.append({"role": "tool", "tool_call_id": calls[0]["id"], "content": json.dumps(result, ensure_ascii=False)})
        else:
            messages.append({"role": "assistant", "content": json.dumps(response, ensure_ascii=False)})
            messages.append({"role": "user", "content": "Platform validation result: " + json.dumps(result, ensure_ascii=False)})

    @staticmethod
    def _readable_response(response: dict[str, Any]) -> dict[str, Any]:
        value = json.loads(json.dumps(response, ensure_ascii=False))
        for call in value.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                try:
                    function["arguments"] = json.loads(function["arguments"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        return value


def smoke_test_world_template(template: dict[str, Any]) -> list[str]:
    """Execute one initial behavior per Agent in a disposable SQLite world."""

    structural = validate_world_template(template)
    if not structural["valid"]:
        return list(structural["errors"])
    errors: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="sim2-world-smoke-") as directory:
            root = Path(directory)
            package = write_world_package_from_template(template, root / "package")
            db_path = create_instance(package, "smoke", root / "runtime")
            platform = Platform(db_path)
            try:
                for agent in platform.store.repositories.entities.list_agents():
                    actor_id = str(agent["id"])
                    view = platform.view.build_decision_view(actor_id, include_diagnostics=True)
                    candidates = [
                        item for item in (view.get("executable_behaviors") or [])
                        if not item.get("target_required") and not item.get("requires_target")
                    ]
                    if not candidates:
                        diagnostics = view.get("unavailable_behavior_diagnostics") or []
                        detail = "; ".join(
                            f"{item.get('behavior_id')}: {', '.join(str(value) for value in (item.get('failed') or []))}"
                            for item in diagnostics[:8]
                        )
                        errors.append(
                            f"runtime smoke: agent {actor_id} has no executable initial behavior"
                            + (f"; {detail}" if detail else "")
                        )
                        continue
                    choice = candidates[0]
                    behavior_ref = str(choice.get("behavior_ref") or choice.get("ref") or "")
                    if not behavior_ref:
                        errors.append(f"runtime smoke: agent {actor_id} executable behavior has no Platform reference")
                        continue
                    started = platform.submit_behavior(BehaviorIntent(
                        actor_id=actor_id,
                        behavior_ref=behavior_ref,
                        target={},
                        parameters=dict(choice.get("parameters") or {}),
                        reason="WorldBuilder runtime smoke validation.",
                        confidence=1.0,
                        intent_id=f"world-builder-smoke:{actor_id}",
                    ))
                    if started.get("status") != "accepted":
                        errors.append(f"runtime smoke: agent {actor_id} could not start {choice.get('name') or behavior_ref}: {started.get('message') or started.get('failed_check')}")
                        continue
                    for _ in range(max(1, int(started.get("remaining_minutes", 1) or 1))):
                        platform.step()
                    outcomes = platform.store.repositories.events.recent_outcomes_for_actor(actor_id, limit=1)
                    if not outcomes or outcomes[0].get("event_type") != "behavior.completed":
                        result = outcomes[0].get("result") if outcomes else "no outcome was recorded"
                        errors.append(f"runtime smoke: agent {actor_id} behavior did not complete: {result}")
            finally:
                platform.close()
    except Exception as exc:
        errors.append(f"runtime smoke could not build or execute the template: {exc}")
    return errors
