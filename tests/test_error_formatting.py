from __future__ import annotations

import json

import httpx
from ouro import (
    BadRequestError,
    ExternalServiceError,
    InternalServerError,
    RouteExecutionError,
    UnprocessableEntityError,
)
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
                "retryable": True,
            },
        },
        status_code=503,
        retryable=True,
        code="external_service_error",
    )

    payload = json.loads(_format_ouro_error(error))

    assert payload["error"] == "external_service_error"
    assert payload["status"] == 503
    assert payload["retryable"] is True
    assert payload["action_id"] == "00000000-0000-0000-0000-000000000001"


def test_bad_request_column_missing_is_actionable_and_non_retryable() -> None:
    body = {
        "data": None,
        "error": {
            "message": 'Query failed: column "mae_ev" does not exist',
            "code": "42703",
            "hint": 'Perhaps you meant to reference the column "MAE_eV".',
        },
    }
    error = BadRequestError(
        'Query failed: column "mae_ev" does not exist',
        response=_response(400, body),
        body=body,
    )

    payload = json.loads(_format_ouro_error(error))

    assert payload["error"] == "invalid_dataset_query"
    assert payload["status"] == 400
    assert payload["retryable"] is False
    assert payload["code"] == "42703"
    assert 'Perhaps you meant to reference the column "MAE_eV".' in payload["hint"]
    assert "snake_case" in payload["hint"]


def test_unprocessable_entity_is_actionable_and_preserves_nested_context() -> None:
    body = {
        "error": {
            "message": "Quest eval configuration is invalid",
            "details": "Pinned input 'reference' is not declared on the route",
            "errors": [
                {
                    "field": "eval_static_inputs.reference",
                    "message": "Unknown route input",
                }
            ],
            "action_id": "00000000-0000-0000-0000-000000000001",
        }
    }
    error = UnprocessableEntityError(
        "Request failed with status 422",
        response=_response(422, body),
        body=body,
    )

    payload = json.loads(_format_ouro_error(error))

    assert payload == {
        "error": "validation_error",
        "message": "Quest eval configuration is invalid",
        "status": 422,
        "retryable": False,
        "details": "Pinned input 'reference' is not declared on the route",
        "errors": [
            {
                "field": "eval_static_inputs.reference",
                "message": "Unknown route input",
            }
        ],
        "action_id": "00000000-0000-0000-0000-000000000001",
    }


def test_unprocessable_entity_preserves_top_level_context() -> None:
    body = {
        "message": "Submission does not match item requirements",
        "details": {"expected_keys": ["structure"], "received_keys": ["file"]},
        "errors": {"file": "Unexpected contributor key"},
        "action_id": "00000000-0000-0000-0000-000000000002",
    }
    error = UnprocessableEntityError(
        "Submission does not match item requirements",
        response=_response(422, body),
        body=body,
    )

    payload = json.loads(_format_ouro_error(error))

    assert payload["error"] == "validation_error"
    assert payload["retryable"] is False
    assert payload["details"] == {
        "expected_keys": ["structure"],
        "received_keys": ["file"],
    }
    assert payload["errors"] == {"file": "Unexpected contributor key"}
    assert payload["action_id"].endswith("0002")
