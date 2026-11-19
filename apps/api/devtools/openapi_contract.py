"""Validating requests/responses against a vendor's own OpenAPI spec
(a subset vendored next to this file). Shared by the HeyGen and
LiveAvatar contracts: the simulators reject anything the vendor's
schema wouldn't accept and refuse to send anything it doesn't describe.
"""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


def _pointer(*parts: str) -> str:
    return "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _coerce(raw: str, schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "integer":
        try:
            return int(raw)
        except ValueError:
            return raw
    if kind == "boolean" and raw in ("true", "false"):
        return raw == "true"
    return raw


class OpenApiContract:
    def __init__(self, spec_path: Path, base_uri: str) -> None:
        self._spec_path = spec_path
        self._base_uri = base_uri

    @cached_property
    def spec(self) -> dict[str, Any]:
        return json.loads(self._spec_path.read_text())

    @cached_property
    def _registry(self) -> Registry:
        resource = Resource.from_contents(self.spec, default_specification=DRAFT202012)
        return Registry().with_resource(self._base_uri, resource)

    def errors(self, pointer: str, instance: Any) -> list[str]:
        validator = Draft202012Validator(
            {"$ref": f"{self._base_uri}#/{pointer}"}, registry=self._registry
        )
        return [
            f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in validator.iter_errors(instance)
        ]

    def _operation(self, path: str, method: str) -> dict[str, Any]:
        return self.spec["paths"][path][method]

    def _parameters(self, path: str, method: str) -> list[dict[str, Any]]:
        params = []
        for param in self._operation(path, method).get("parameters", []):
            if "$ref" in param:
                param = self.spec["components"]["parameters"][param["$ref"].rsplit("/", 1)[1]]
            params.append(param)
        return params

    def request_body_errors(self, path: str, method: str, body: Any) -> list[str]:
        content = self._operation(path, method)["requestBody"]["content"]["application/json"]
        if "$ref" in content["schema"]:
            return self.errors(content["schema"]["$ref"].lstrip("#/"), body)
        return self.errors(
            _pointer("paths", path, method, "requestBody", "content", "application/json", "schema"),
            body,
        )

    def response_errors(self, path: str, method: str, status: int, body: Any) -> list[str]:
        return self.errors(
            _pointer(
                "paths", path, method, "responses", str(status), "content", "application/json",
                "schema",
            ),
            body,
        )

    def parameter_errors(
        self, path: str, method: str, query: dict[str, str], headers: dict[str, str]
    ) -> list[str]:
        """Unknown query parameters count as errors too: a vendor might
        silently ignore a misspelled one, which is exactly the kind of
        bug (a filter that never applies) this is here to catch."""
        errors = []
        params = self._parameters(path, method)
        known_query = {p["name"] for p in params if p["in"] == "query"}
        errors += [f"unknown query parameter {name!r}" for name in query if name not in known_query]
        lowered_headers = {k.lower(): v for k, v in headers.items()}
        for param in params:
            if param["in"] == "path":
                continue
            source = query if param["in"] == "query" else lowered_headers
            key = param["name"] if param["in"] == "query" else param["name"].lower()
            if key not in source:
                if param.get("required"):
                    errors.append(f"missing required {param['in']} parameter {param['name']!r}")
                continue
            schema = param.get("schema", {})
            instance = _coerce(source[key], schema)
            validator = Draft202012Validator(schema)
            errors += [f"{param['name']}: {e.message}" for e in validator.iter_errors(instance)]
        return errors
