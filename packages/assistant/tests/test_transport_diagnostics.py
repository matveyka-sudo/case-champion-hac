"""TEST_ONLY: bounded diagnostic codes never expose provider/key/request content."""

import logging

import httpx2
import pytest
from a2_assistant.transport import FlashConfig, ModelFailure, SDKTransport
from openai import DefaultAsyncHttpxClient

SECRET = "TEST_ONLY-private-provider-message"


def response_body():
    return {
        "id": "TEST_ONLY",
        "object": "chat.completion",
        "created": 0,
        "model": "TEST_ONLY",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_stage", "expected_exception", "expected_cause"),
    [
        ("connection", "sdk_request", "APIConnectionError", "ConnectError"),
        ("choices", "response_choices", "ModelFailure", "other"),
        ("reply", "reply_validation", "ValidationError", "other"),
        ("unknown_class", "sdk_request", "other", "other"),
    ],
)
async def test_invalid_response_diagnostics_are_fixed_codes_only(
    failure, expected_stage, expected_exception, expected_cause, caplog
):
    class TEST_ONLY_PRIVATE_EXCEPTION(Exception):
        pass

    def handle(request):
        if failure == "connection":
            raise httpx2.ConnectError(SECRET)
        if failure == "unknown_class":
            raise TEST_ONLY_PRIVATE_EXCEPTION(SECRET)
        body = response_body()
        if failure == "choices":
            body["choices"] = []
        if failure == "reply":
            body["choices"][0]["message"]["content"] = SECRET * 1000
        return httpx2.Response(200, json=body)

    config = FlashConfig(api_key=SECRET, folder_id="TEST_ONLY_FOLDER")
    transport = SDKTransport(
        config, http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handle))
    )
    caplog.set_level(logging.INFO, logger="a2_assistant")
    try:
        with pytest.raises(ModelFailure) as caught:
            await transport.complete(
                [{"role": "user", "content": SECRET}],
                [],
                model=config.model_uri,
                max_tokens=100,
                timeout=1,
            )
    finally:
        await transport.aclose()
    assert caught.value.code == "invalid_response" and caught.value.status_code is None
    record = next(
        record
        for record in caplog.records
        if record.msg.startswith("assistant_transport_invalid_response")
    )
    message = record.getMessage()
    assert f"stage={expected_stage}" in message
    assert f"exception_type={expected_exception}" in message
    assert f"cause_type={expected_cause}" in message
    assert SECRET not in message and "TEST_ONLY_FOLDER" not in message
    assert "TEST_ONLY_PRIVATE_EXCEPTION" not in message
    assert len(message) < 250
