"""Checks requests/responses against HeyGen's own v3 OpenAPI schemas.

heygen_v3_subset.json is cut from HeyGen's published spec
(https://developers.heygen.com/openapi/external-api.json) down to the
endpoints Cindra calls plus every component they reference. Both the
simulator and the contract tests validate against it, so a field-name
or parameter mistake in app/tool_integrations/heygen.py fails here
rather than on a customer's first paid render.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_SPEC_PATH = Path(__file__).with_name("heygen_v3_subset.json")
_BASE_URI = "urn:heygen-v3"


@cache
def spec() -> dict[str, Any]:
    return json.loads(_SPEC_PATH.read_text())


@cache
def _registry() -> Registry:
    resource = Resource.from_contents(spec(), default_specification=DRAFT202012)
    return Registry().with_resource(_BASE_URI, resource)


def _pointer(*parts: str) -> str:
    return "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _errors(pointer: str, instance: Any) -> list[str]:
    validator = Draft202012Validator(
        {"$ref": f"{_BASE_URI}#/{pointer}"}, registry=_registry()
    )
    return [
        f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in validator.iter_errors(instance)
    ]


def _operation(path: str, method: str) -> dict[str, Any]:
    return spec()["paths"][path][method]


def _parameters(path: str, method: str) -> list[dict[str, Any]]:
    params = []
    for param in _operation(path, method).get("parameters", []):
        if "$ref" in param:
            param = spec()["components"]["parameters"][param["$ref"].rsplit("/", 1)[1]]
        params.append(param)
    return params


def request_body_errors(path: str, method: str, body: Any) -> list[str]:
    content = _operation(path, method)["requestBody"]["content"]["application/json"]
    if "$ref" in content["schema"]:
        return _errors(content["schema"]["$ref"].lstrip("#/"), body)
    return _errors(
        _pointer("paths", path, method, "requestBody", "content", "application/json", "schema"),
        body,
    )


def response_errors(path: str, method: str, status: int, body: Any) -> list[str]:
    return _errors(
        _pointer(
            "paths", path, method, "responses", str(status), "content", "application/json", "schema"
        ),
        body,
    )


def _coerce(raw: str, schema: dict[str, Any]) -> Any:
    if schema.get("type") == "integer":
        try:
            return int(raw)
        except ValueError:
            return raw
    return raw


def parameter_errors(
    path: str, method: str, query: dict[str, str], headers: dict[str, str]
) -> list[str]:
    """Unknown query parameters count as errors too: HeyGen might
    silently ignore a misspelled one, which is exactly the kind of bug
    (a filter that never applies) this is here to catch."""
    errors = []
    params = _parameters(path, method)
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
