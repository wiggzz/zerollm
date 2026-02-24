"""Unit tests for AWS handler response serialization."""

from __future__ import annotations

import json
from decimal import Decimal

from control_plane.backends.aws import handlers


def test_api_response_serializes_decimal_values():
    response = handlers._api_response(200, {"idle_timeout": Decimal("1"), "util": Decimal("1.5")})
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["idle_timeout"] == 1
    assert body["util"] == 1.5


def test_api_response_preserves_string_body_for_sse():
    payload = "data: {\"id\":\"chunk-1\"}\n\ndata: [DONE]\n\n"
    response = handlers._api_response(
        200,
        payload,
        headers={"Content-Type": "text/event-stream; charset=utf-8"},
    )

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "text/event-stream; charset=utf-8"
    assert response["body"] == payload
