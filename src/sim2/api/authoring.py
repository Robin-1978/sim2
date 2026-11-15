from __future__ import annotations

import tempfile
import re
from pathlib import Path
from typing import Any

import yaml

from ..bootstrap.service import (
    describe_config,
    validate_world_template,
    validate_config,
    world_template_schema,
    world_package_template,
    write_world_package_from_template,
    create_instance as bootstrap_create_instance,
)
from ..platform.runtime import Platform
from ..llm.gateway import LLMRouter
from ..world_builder.service import WorldBuilderError, WorldBuilderService


class AuthoringService:
    """Transport-neutral authoring operations for the browser workbench.

    The service deliberately works on an in-memory template and disposable
    packages.  It delegates all structural and execution checks to the same
    bootstrap/compiler code used by the CLI; it never opens or mutates a live
    world instance.
    """

    def __init__(self, authoring_root: Path | None = None, runtime_root: Path | None = None) -> None:
        self.authoring_root = authoring_root.resolve() if authoring_root is not None else None
        self.runtime_root = runtime_root.resolve() if runtime_root is not None else (Path.home() / ".sim2").resolve()

    def list_packages(self) -> dict[str, Any]:
        """List selectable declarative packages under the configured root."""

        if self.authoring_root is None:
            return {"status": "rejected", "errors": ["an authoring_root is required to list world packages"], "packages": []}
        packages: list[dict[str, Any]] = []
        candidates = [self.authoring_root] + sorted(item for item in self.authoring_root.iterdir() if item.is_dir())
        for path in candidates:
            manifest_path = path / "manifest.yaml"
            if not manifest_path.is_file():
                continue
            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
                validation = validate_config(path)
                packages.append({
                    "id": str(manifest.get("world_id") or path.name),
                    "name": str(manifest.get("name") or manifest.get("world_id") or path.name),
                    "path": str(path),
                    "config_version": str(manifest.get("config_version") or "unknown"),
                    "status": "passed" if validation.get("valid") else "rejected",
                    "errors": list(validation.get("errors") or []),
                })
            except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                packages.append({"id": path.name, "name": path.name, "path": str(path), "status": "rejected", "errors": [str(exc)]})
        return {"status": "ok", "packages": packages}

    def create_instance(self, package_id: str, instance_id: str, *, runtime_root: Path | None = None) -> dict[str, Any]:
        """Create an independent SQLite instance from a catalog package."""

        catalog = self.list_packages()
        wanted = str(package_id or "").strip()
        package = next((item for item in catalog.get("packages", []) if item.get("id") == wanted), None)
        if package is None:
            return {"status": "rejected", "errors": [f"unknown world package: {wanted}"], "packages": catalog.get("packages", [])}
        if package.get("status") != "passed":
            return {"status": "rejected", "errors": list(package.get("errors") or ["selected package is invalid"]), "package": package}
        normalized_instance = str(instance_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", normalized_instance):
            return {"status": "rejected", "errors": ["instance_id must contain 1-64 letters, numbers, underscores or hyphens"]}
        destination = Path(runtime_root).resolve() if runtime_root is not None else self.runtime_root
        try:
            db_path = bootstrap_create_instance(Path(str(package["path"])), normalized_instance, destination)
        except (FileExistsError, OSError, TypeError, ValueError) as exc:
            return {"status": "rejected", "errors": [str(exc)], "package": package}
        return {
            "status": "passed",
            "package": {"id": package["id"], "name": package["name"], "path": package["path"]},
            "instance_id": normalized_instance,
            "runtime_root": str(destination),
            "db_path": str(db_path),
            "start_command": f"sim2 serve --db {db_path}",
        }

    def _check_path(self, config_dir: Path, *, trusted: bool = False) -> Path:
        resolved = config_dir.resolve()
        if trusted:
            return resolved
        if self.authoring_root is None:
            raise ValueError("an authoring_root is required to open a path")
        try:
            resolved.relative_to(self.authoring_root)
        except ValueError as exc:
            raise ValueError("config path is outside the configured authoring_root") from exc
        return resolved

    def open_package(self, config_dir: Path, *, trusted: bool = False) -> dict[str, Any]:
        """Load an existing package without touching its SQLite instance."""

        try:
            path = self._check_path(Path(config_dir), trusted=trusted)
        except (TypeError, ValueError, OSError) as exc:
            errors = [str(exc)]
            return {"status": "rejected", "errors": errors, "diagnostics": self.diagnostics(errors)}
        if not path.is_dir():
            return {"status": "rejected", "errors": [f"config directory does not exist: {path}"]}
        validation = validate_config(path)
        template = world_package_template(path)
        errors = list(validation["errors"])
        return {
            "status": "passed" if validation["valid"] else "rejected",
            "source_path": str(path),
            "template": template,
            "errors": errors,
            "diagnostics": self.diagnostics(errors),
            "description": {
                "world_id": (validation.get("manifest") or {}).get("world_id"),
                "config_version": (validation.get("manifest") or {}).get("config_version"),
            },
        }

    def schema(self) -> dict[str, Any]:
        return {"status": "ok", "schema": world_template_schema()}

    def build_from_brief(self, brief: str, *, provider_name: str | None = None, provider: Any = None) -> dict[str, Any]:
        """Run the existing bounded WorldBuilder behind an explicit UI action.

        Provider construction stays server-side, so credentials and provider
        metadata never reach the browser.  The returned audit is deliberately
        limited to iteration/usage and does not expose hidden reasoning.
        """

        if not str(brief or "").strip():
            return {"status": "rejected", "errors": ["a non-empty world brief is required"]}
        try:
            if provider is None:
                router = LLMRouter.from_default_config()
                provider = router.new_provider_for_name(provider_name, role="world_builder") if provider_name else router.provider_for_role("world_builder")
            draft = WorldBuilderService(provider).build(str(brief))
        except (WorldBuilderError, ValueError, OSError, KeyError) as exc:
            usage = getattr(exc, "usage", {}) or {}
            return {"status": "rejected", "errors": [str(exc)], "usage": usage}
        return {
            "status": "passed",
            "template": draft.template,
            "audit": {"iterations": draft.iterations, "usage": draft.usage},
        }

    @staticmethod
    def diagnostics(errors: list[str]) -> list[dict[str, str]]:
        return [
            {
                "code": "template_validation_error",
                "path": str(error).split(" ", 1)[0] or "template",
                "message": str(error),
            }
            for error in errors
        ]

    @staticmethod
    def _template(value: Any, source: Any = None) -> tuple[dict[str, Any] | None, list[str]]:
        raw = value if value is not None else source
        if isinstance(raw, dict):
            return dict(raw), []
        if isinstance(raw, str):
            try:
                parsed = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                return None, [f"source is not valid YAML: {exc}"]
            if isinstance(parsed, dict):
                return parsed, []
        return None, ["template must be a JSON object or source must contain a YAML object"]

    def compile(self, value: Any = None, *, source: Any = None, source_path: str | None = None, draft_revision: int = 0) -> dict[str, Any]:
        template, parse_errors = self._template(value, source)
        revision = max(0, int(draft_revision or 0))
        if template is None:
            return {"status": "rejected", "draft_revision": revision, "errors": parse_errors, "diagnostics": self.diagnostics(parse_errors), "warnings": []}
        strict_validation = validate_world_template(template)
        existing_package = bool(source_path)
        validation = strict_validation
        if existing_package:
            validation = {"valid": True, "errors": []}
        if not validation["valid"]:
            return {
                "status": "rejected",
                "draft_revision": revision,
                "errors": validation["errors"],
                "diagnostics": self.diagnostics(validation["errors"]),
                "warnings": [],
            }
        try:
            with tempfile.TemporaryDirectory(prefix="sim2-authoring-compile-") as directory:
                package = write_world_package_from_template(template, Path(directory) / "world", overwrite=True, validate_template=not existing_package)
                description = describe_config(package)
                files = sorted(path.relative_to(package).as_posix() for path in package.rglob("*.yaml"))
        except (TypeError, ValueError, OSError) as exc:
            return {
                "status": "rejected",
                "draft_revision": revision,
                "errors": [str(exc)],
                "diagnostics": self.diagnostics([str(exc)]),
                "warnings": [],
            }
        return {
            "status": "passed",
            "draft_revision": revision,
            "errors": [],
            "diagnostics": [],
            "warnings": (
                [f"Imported package passed package validation; {len(strict_validation['errors'])} unified-template advisories are source-format specific."]
                if existing_package and strict_validation["errors"] else []
            ),
            "world_id": str((template.get("world") or {}).get("id") or ""),
            "description": description,
            "files": files,
        }

    def smoke(self, value: Any = None, *, source: Any = None, source_path: str | None = None, draft_revision: int = 0, minutes: int = 1) -> dict[str, Any]:
        compiled = self.compile(value, source=source, source_path=source_path, draft_revision=draft_revision)
        if compiled.get("status") != "passed":
            return {**compiled, "phase": "compile", "smoke": None}
        template, _ = self._template(value, source)
        assert template is not None
        steps = max(1, min(int(minutes), 30))
        try:
            with tempfile.TemporaryDirectory(prefix="sim2-authoring-smoke-") as directory:
                root = Path(directory)
                package = write_world_package_from_template(template, root / "world", overwrite=True, validate_template=not bool(source_path))
                db_path = bootstrap_create_instance(package, "authoring-smoke", root / "runtime")
                platform = Platform(db_path)
                try:
                    steps_run = [platform.step() for _ in range(steps)]
                    observer = platform.observer_view(event_limit=12)
                    timeline = platform.timeline(limit=24)
                finally:
                    platform.close()
        except (TypeError, ValueError, OSError, KeyError) as exc:
            return {
                **compiled,
                "status": "rejected",
                "phase": "smoke",
                "errors": [str(exc)],
                "diagnostics": self.diagnostics([str(exc)]),
                "smoke": None,
            }
        return {
            **compiled,
            "phase": "smoke",
            "smoke": {
                "status": "passed",
                "minutes": steps,
                "last_step": steps_run[-1],
                "world_minute": observer.get("world_time"),
                "event_types": sorted({str(item.get("event_type")) for item in timeline.get("events", [])}),
                "agent_count": len(observer.get("agents", [])),
            },
        }

    def export(self, value: Any = None, *, source: Any = None, source_path: str | None = None, draft_revision: int = 0) -> dict[str, Any]:
        compiled = self.compile(value, source=source, source_path=source_path, draft_revision=draft_revision)
        if compiled.get("status") != "passed":
            return compiled
        template, _ = self._template(value, source)
        assert template is not None
        try:
            with tempfile.TemporaryDirectory(prefix="sim2-authoring-export-") as directory:
                package = write_world_package_from_template(template, Path(directory) / "world", overwrite=True, validate_template=not bool(source_path))
                files = {
                    path.relative_to(package).as_posix(): path.read_text(encoding="utf-8")
                    for path in sorted(package.rglob("*.yaml"))
                }
        except (TypeError, ValueError, OSError) as exc:
            return {**compiled, "status": "rejected", "errors": [str(exc)], "diagnostics": self.diagnostics([str(exc)])}
        return {**compiled, "package_format": "yaml-files", "files": files}

