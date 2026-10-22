"""Checks requests/responses against Google's own Drive v3 discovery
document (the cut in google_drive_v3_subset.json), so the simulator can
refuse anything the real API wouldn't describe -- a misspelled query
parameter, a field selection naming a field File doesn't have, a body
key Drive doesn't know.

Discovery documents aren't JSON Schema, so this walks them directly:
`properties`, `$ref`, `items`, `additionalProperties` and `type` are
all Cindra's calls need.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

_SPEC_PATH = Path(__file__).with_name("google_drive_v3_subset.json")
_JSON_TYPES = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "array": list,
    "object": dict,
}


@cache
def spec() -> dict[str, Any]:
    return json.loads(_SPEC_PATH.read_text())


def method(resource: str, name: str) -> dict[str, Any]:
    return spec()["resources"][resource]["methods"][name]


def _resolve(node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = spec()["schemas"][node["$ref"]]
    return node


def schema(name: str) -> dict[str, Any]:
    return spec()["schemas"][name]


def parameter_errors(resource: str, name: str, params: dict[str, str]) -> list[str]:
    """Query/path parameters the method doesn't define, required ones
    missing, and values a boolean or enum parameter can't take."""
    declared = {**spec()["parameters"], **method(resource, name).get("parameters", {})}
    errors = [f"unknown parameter {key!r}" for key in params if key not in declared]
    for key, definition in declared.items():
        if definition.get("required") and key not in params:
            errors.append(f"missing required parameter {key!r}")
        if key not in params:
            continue
        value = params[key]
        if definition.get("type") == "boolean" and value not in ("true", "false"):
            errors.append(f"{key!r} must be true/false, got {value!r}")
        if "enum" in definition and value not in definition["enum"]:
            errors.append(f"{key!r} must be one of {definition['enum']}, got {value!r}")
    return errors


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced ')'")
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if depth != 0:
        raise ValueError("unbalanced '('")
    parts.append("".join(current).strip())
    return parts


def parse_fields(expr: str) -> dict[str, Any]:
    """Google's partial-response syntax -> nested dict of selected
    fields: "id,user(displayName),a/b" -> {"id": {}, "user":
    {"displayName": {}}, "a": {"b": {}}}."""
    tree: dict[str, Any] = {}
    for part in _split_top_level(expr):
        if not part:
            raise ValueError("empty field selection")
        if "(" in part:
            if not part.endswith(")"):
                raise ValueError(f"bad selection {part!r}")
            head, inner = part.split("(", 1)
            target = tree
            for segment in head.split("/"):
                target = target.setdefault(segment, {})
            target.update(parse_fields(inner[:-1]))
        else:
            target = tree
            for segment in part.split("/"):
                target = target.setdefault(segment, {})
    return tree


def _field_errors(node: dict[str, Any], tree: dict[str, Any], path: str) -> list[str]:
    node = _resolve(node)
    if node.get("type") == "array":
        node = _resolve(node.get("items", {}))
    errors = []
    properties = node.get("properties", {})
    for key, sub in tree.items():
        if key == "*":
            continue
        if key not in properties and "additionalProperties" not in node:
            errors.append(f"invalid field selection {path + key!r}")
            continue
        if sub:
            child = properties.get(key) or node.get("additionalProperties", {})
            errors.extend(_field_errors(child, sub, f"{path}{key}/"))
    return errors


def fields_errors(schema_name: str, expr: str) -> list[str]:
    try:
        tree = parse_fields(expr)
    except ValueError as exc:
        return [f"invalid field selection: {exc}"]
    return _field_errors(schema(schema_name), tree, "")


def _child(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _value_errors(node: dict[str, Any], value: Any, path: str) -> list[str]:
    node = _resolve(node)
    expected = _JSON_TYPES.get(node.get("type", ""))
    if expected is not None and not isinstance(value, expected):
        return [f"{path or '<root>'}: expected {node['type']}, got {type(value).__name__}"]
    if node.get("format") == "int64" and isinstance(value, str) and not value.isdigit():
        return [f"{path}: expected an int64 string, got {value!r}"]
    errors: list[str] = []
    if isinstance(value, dict):
        properties = node.get("properties", {})
        extra = node.get("additionalProperties")
        for key, item in value.items():
            if key in properties:
                errors.extend(_value_errors(properties[key], item, _child(path, key)))
            elif extra is not None:
                errors.extend(_value_errors(extra, item, _child(path, key)))
            else:
                errors.append(f"unknown field {_child(path, key)!r}")
    elif isinstance(value, list) and "items" in node:
        for index, item in enumerate(value):
            errors.extend(_value_errors(node["items"], item, f"{path}[{index}]"))
    return errors


def body_errors(schema_name: str, body: Any) -> list[str]:
    """Unknown keys and wrong value types in a request body / response."""
    return _value_errors(schema(schema_name), body, "")
