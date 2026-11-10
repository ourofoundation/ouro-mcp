"""Compact error context on a failed action: the reason must be readable
without the full response body, whichever keys the service used."""

from ouro_mcp.tools.services import (
    ERROR_MESSAGE_MAX_CHARS,
    _action_error_context,
    _error_details,
)


def test_message_and_code_keys_are_reported():
    context = _action_error_context(
        {"error": {"message": "upstream said no", "code": "external_service_error", "status": 502}}
    )
    assert context["message"] == "upstream said no"
    assert context["code"] == "external_service_error"
    assert context["error_type"] == "external_service_error"
    assert context["status_code"] == 502
    assert context["retryable"] is True


def test_service_error_text_and_error_code_are_reported():
    # The shape the DFT routes return for an SCF that will not converge.
    response = {
        "error": {
            "error": "ABACUS convergence failed after 200 iterations",
            "status": "error",
            "error_code": "scf_not_converged",
            "property": "scf",
            "suggested_retry": {"mixing_beta": 0.05},
        }
    }
    context = _action_error_context(response)
    assert context["error_type"] == "route_execution_failed"
    assert context["message"] == "ABACUS convergence failed after 200 iterations"
    assert context["code"] == "scf_not_converged"
    # A non-numeric status is not a status code.
    assert "status_code" not in context
    # The rest of the payload is still there for include_response.
    assert _error_details(response) == response


def test_detail_is_the_last_fallback():
    assert _action_error_context({"detail": "Not found"})["message"] == "Not found"


def test_long_message_is_shortened_but_kept_in_the_details():
    text = "x" * (ERROR_MESSAGE_MAX_CHARS + 50)
    response = {"error": {"message": text}}
    context = _action_error_context(response)
    assert len(context["message"]) == ERROR_MESSAGE_MAX_CHARS
    assert context["message"].endswith("…")
    assert _error_details(response) == response


def test_fully_summarized_error_has_no_details():
    assert _error_details({"error": {"message": "short", "code": "x"}}) is None
