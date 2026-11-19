"""Checks requests/responses against HeyGen's own v3 OpenAPI schemas.

heygen_v3_subset.json is cut from HeyGen's published spec
(https://developers.heygen.com/openapi/external-api.json) down to the
endpoints Cindra calls plus every component they reference. Both the
simulator and the contract tests validate against it, so a field-name
or parameter mistake in app/tool_integrations/heygen.py fails here
rather than on a customer's first paid render.
"""

from __future__ import annotations

from pathlib import Path

from devtools.openapi_contract import OpenApiContract

_CONTRACT = OpenApiContract(Path(__file__).with_name("heygen_v3_subset.json"), "urn:heygen-v3")

request_body_errors = _CONTRACT.request_body_errors
response_errors = _CONTRACT.response_errors
parameter_errors = _CONTRACT.parameter_errors
