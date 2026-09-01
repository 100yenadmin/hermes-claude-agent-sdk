from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from hermes_claude_agent_sdk.turn_input import (
    MAX_IMAGE_BYTES,
    SDKTurnInput,
    TurnInputValidationError,
    build_sdk_turn_input,
)


_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode("ascii")


def _request(content):
    return SimpleNamespace(messages=({"role": "user", "content": content},))


def test_text_input_stays_a_string() -> None:
    assert build_sdk_turn_input(_request("  hello  ")) == "hello"


def test_image_url_data_uri_becomes_one_sdk_user_message() -> None:
    prompt = build_sdk_turn_input(
        _request(
            [
                {"type": "text", "text": "inspect the fixture"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_PNG}"},
                },
            ]
        )
    )

    assert isinstance(prompt, SDKTurnInput)
    message = prompt.as_sdk_message()
    assert message["type"] == "user"
    assert message["message"]["content"] == [
        {"type": "text", "text": "inspect the fixture"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": _PNG,
            },
        },
    ]


def test_image_only_turn_gets_a_neutral_text_prompt() -> None:
    prompt = build_sdk_turn_input(
        _request(
            [
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{_PNG}",
                }
            ]
        )
    )
    assert isinstance(prompt, SDKTurnInput)
    assert prompt.as_sdk_message()["message"]["content"][0] == {
        "type": "text",
        "text": "What do you see in this image?",
    }


@pytest.mark.parametrize(
    "url",
    [
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/png;base64,not-valid!",
        "file:///tmp/private.png",
        "http://example.test/image.png",
        "https://user:password@example.test/image.png",
    ],
)
def test_unsafe_or_malformed_image_fails_closed(url: str) -> None:
    with pytest.raises(
        TurnInputValidationError, match="claude_runtime_image_invalid"
    ):
        build_sdk_turn_input(
            _request([{"type": "image_url", "image_url": {"url": url}}])
        )


def test_oversized_image_fails_before_decoding() -> None:
    oversized = "A" * ((((MAX_IMAGE_BYTES + 2) // 3) * 4) + 1)
    with pytest.raises(
        TurnInputValidationError, match="claude_runtime_image_too_large"
    ):
        build_sdk_turn_input(
            _request(
                [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{oversized}"
                        },
                    }
                ]
            )
        )


def test_repr_never_contains_prompt_image_or_url() -> None:
    prompt = build_sdk_turn_input(
        _request(
            [
                {"type": "text", "text": "private prompt marker"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_PNG}"},
                },
            ]
        )
    )
    rendered = repr(prompt)
    assert "private prompt marker" not in rendered
    assert _PNG not in rendered


def test_bounded_https_image_url_maps_without_local_fetch() -> None:
    prompt = build_sdk_turn_input(
        _request(
            [
                {"type": "text", "text": "inspect"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.test/fixture.png"},
                },
            ]
        )
    )
    assert isinstance(prompt, SDKTurnInput)
    assert prompt.as_sdk_message()["message"]["content"][1] == {
        "type": "image",
        "source": {"type": "url", "url": "https://example.test/fixture.png"},
    }
