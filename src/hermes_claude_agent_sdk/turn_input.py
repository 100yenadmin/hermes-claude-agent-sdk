"""Validated conversion from a host turn into a Claude SDK prompt.

Hermes represents native vision input in the last public user message using
OpenAI-compatible ``image_url`` blocks.  The Claude Agent SDK public client
accepts an ``AsyncIterable`` of user messages, whose content may use Claude
image source blocks.  This module is the narrow boundary between those two
public shapes.

Raw prompt and image values are deliberately excluded from reprs.  Callers
must not serialize :class:`SDKTurnInput` into evidence or logs.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit


MAX_TURN_TEXT = 32_000
MAX_IMAGES_PER_TURN = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_URL_LENGTH = 2_048

_ALLOWED_MEDIA_TYPES = frozenset(
    {"image/gif", "image/jpeg", "image/png", "image/webp"}
)
_DATA_URL = re.compile(
    r"\Adata:(image/(?:gif|jpeg|png|webp));base64,([A-Za-z0-9+/]*={0,2})\Z"
)


class TurnInputValidationError(ValueError):
    """A fail-closed, content-free turn-input validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SDKImageSource:
    """One bounded image source; its payload is always hidden from repr."""

    source_type: str
    media_type: str | None = None
    value: str = field(default="", repr=False)
    decoded_bytes: int = field(default=0, repr=False)

    def as_content_block(self) -> dict[str, Any]:
        if self.source_type == "base64":
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": self.media_type,
                    "data": self.value,
                },
            }
        return {
            "type": "image",
            "source": {"type": "url", "url": self.value},
        }


@dataclass(frozen=True, slots=True)
class SDKTurnInput:
    """Exact, validated rich turn accepted by :class:`SDKSession`."""

    text: str = field(repr=False)
    images: tuple[SDKImageSource, ...] = field(repr=False)

    def as_sdk_message(self) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": self.text}]
        content.extend(image.as_content_block() for image in self.images)
        return {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
        }

    async def _messages(self) -> AsyncIterator[dict[str, Any]]:
        yield self.as_sdk_message()

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._messages()


def _bounded_text(content: Any) -> str | None:
    if isinstance(content, str):
        text = content.strip()
        return text[:MAX_TURN_TEXT] if text else None
    if not isinstance(content, Sequence) or isinstance(
        content, (str, bytes, bytearray)
    ):
        return None
    parts: list[str] = []
    used = 0
    for block in content:
        if not isinstance(block, Mapping) or block.get("type") != "text":
            continue
        value = block.get("text")
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        remaining = MAX_TURN_TEXT - used
        if remaining <= 0:
            break
        part = value[:remaining]
        parts.append(part)
        used += len(part)
    text = "\n".join(parts).strip()
    return text if text else None


def _base64_source(media_type: Any, value: Any) -> SDKImageSource:
    if media_type not in _ALLOWED_MEDIA_TYPES or not isinstance(value, str) or not value:
        raise TurnInputValidationError("claude_runtime_image_invalid")
    max_encoded = ((MAX_IMAGE_BYTES + 2) // 3) * 4
    if len(value) > max_encoded:
        raise TurnInputValidationError("claude_runtime_image_too_large")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise TurnInputValidationError("claude_runtime_image_invalid") from None
    if not decoded:
        raise TurnInputValidationError("claude_runtime_image_invalid")
    if len(decoded) > MAX_IMAGE_BYTES:
        raise TurnInputValidationError("claude_runtime_image_too_large")
    canonical = base64.b64encode(decoded).decode("ascii")
    return SDKImageSource(
        source_type="base64",
        media_type=media_type,
        value=canonical,
        decoded_bytes=len(decoded),
    )


def _url_source(value: Any) -> SDKImageSource:
    if not isinstance(value, str) or not value or len(value) > MAX_IMAGE_URL_LENGTH:
        raise TurnInputValidationError("claude_runtime_image_invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise TurnInputValidationError("claude_runtime_image_invalid")
    return SDKImageSource(source_type="url", value=value)


def _image_source(block: Mapping[str, Any]) -> SDKImageSource | None:
    block_type = block.get("type")
    if block_type in {"image_url", "input_image"}:
        image_url = block.get("image_url")
        if isinstance(image_url, Mapping):
            image_url = image_url.get("url")
        if not isinstance(image_url, str):
            raise TurnInputValidationError("claude_runtime_image_invalid")
        match = _DATA_URL.fullmatch(image_url)
        if match is not None:
            return _base64_source(match.group(1), match.group(2))
        return _url_source(image_url)
    if block_type != "image":
        return None
    source = block.get("source")
    if not isinstance(source, Mapping):
        raise TurnInputValidationError("claude_runtime_image_invalid")
    source_type = source.get("type")
    if source_type == "base64":
        return _base64_source(source.get("media_type"), source.get("data"))
    if source_type == "url":
        return _url_source(source.get("url"))
    raise TurnInputValidationError("claude_runtime_image_invalid")


def build_sdk_turn_input(request: Any) -> str | SDKTurnInput | None:
    """Return a bounded text or rich SDK prompt from the last user message."""

    messages = getattr(request, "messages", ())
    if not isinstance(messages, Sequence) or isinstance(
        messages, (str, bytes, bytearray)
    ):
        return None
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        text = _bounded_text(content)
        if isinstance(content, str):
            return text
        if not isinstance(content, Sequence) or isinstance(
            content, (str, bytes, bytearray)
        ):
            return None
        images: list[SDKImageSource] = []
        total_bytes = 0
        for block in content:
            if not isinstance(block, Mapping):
                continue
            image = _image_source(block)
            if image is None:
                continue
            images.append(image)
            total_bytes += image.decoded_bytes
            if len(images) > MAX_IMAGES_PER_TURN:
                raise TurnInputValidationError("claude_runtime_too_many_images")
            if total_bytes > MAX_TOTAL_IMAGE_BYTES:
                raise TurnInputValidationError("claude_runtime_images_too_large")
        if not images:
            return text
        return SDKTurnInput(
            text=text or "What do you see in this image?",
            images=tuple(images),
        )
    return None
