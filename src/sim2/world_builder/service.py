from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

import yaml

from ..agents.tools import parse_tool_call, tool_schemas_for_role
from ..bootstrap.service import validate_world_template


@dataclass(frozen=True)
class WorldBuildDraft:
    template: dict[str, Any]
    iterations: int
    transcript: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class WorldBuilderService:
    """Creation-time bounded tool loop for a new declarative world package."""

    def __init__(self, provider: Any, *, max_iterations: int = 8):
        self.provider = provider
        self.max_iterations = max(2, int(max_iterations))

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
                result = {
                    "status": "ok",
                    "valid": report["valid"],
                    "errors": report["errors"][:40],
                    "message": "Template is valid; submit this exact template." if report["valid"] else "Correct every error and validate the complete revised template again.",
                }
                validated_digest = self._digest(template) if report["valid"] else ""
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
        raise RuntimeError("WorldBuilder reached its iteration limit without an exact validated template.")

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
