from __future__ import annotations

import json

import httpx
from ouro import ExternalServiceError, InternalServerError, RouteExecutionError
from ouro_mcp.errors import _format_ouro_error


def _response(status_code: int, body: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://api.example.test/routes/use")
    return httpx.Response(status_code, json=body, request=request)


def test_internal_server_error_includes_server_detail() -> None:
    error = InternalServerError(
        "Service unavailable",
        response=_response(
            503,
            {"error": {"message": "Service unavailable", "status": 503}},
        ),
        body={"error": {"message": "Service unavailable", "status": 503}},
    )

    payload = json.loads(_format_ouro_error(error))

    assert payload == {
        "error": "server_error",
        "message": "Service unavailable",
        "status": 503,
        "retryable": True,
    }


def test_route_execution_error_includes_action_response() -> None:
    error = RouteExecutionError(
        "Action failed",
        action_id="00000000-0000-0000-0000-000000000001",
        status="error",
        response={"error": {"message": "Service unavailable", "status": 503}},
    )

    payload = json.loads(_format_ouro_error(error))

    assert payload == {
        "error": "route_execution_failed",
        "message": "Action failed",
        "response": {"error": {"message": "Service unavailable", "status": 503}},
        "action_id": "00000000-0000-0000-0000-000000000001",
        "action_status": "error",
    }


def test_external_service_error_is_actionable() -> None:
    error = ExternalServiceError(
        "Action failed: Service unavailable",
        action_id="00000000-0000-0000-0000-000000000001",
        status="error",
        response={
            "statusCode": 503,
            "error": {
                "type": "external_service_error",
                "code": "external_service_error",
                "message": "Service unavailable",
                "status": 503,
                "serviceUrl": "https://service.example.test",
                "retryable": True,
            },
        },
        status_code=503,
        service_url="https://service.example.test",
        retryable=True,
        code="external_service_error",
    )

    payload = json.loads(_format_ouro_error(error))

    assert payload["error"] == "external_service_error"
    assert payload["status"] == 503
    assert payload["retryable"] is True
    assert payload["service_url"] == "https://service.example.test"
    assert payload["action_id"] == "00000000-0000-0000-0000-000000000001"
