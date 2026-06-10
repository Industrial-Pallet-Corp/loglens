"""Anthropic Claude vision extractor (structured tool output)."""

from __future__ import annotations

import base64
import time

from ..config import ExtractionConfig
from ..models import Sheet
from .base import (
    SYSTEM_PROMPT,
    TOOL,
    TOOL_CHOICE,
    USER_PROMPT,
    ExtractionParseError,
    parse_sheet_json,
    parse_sheet_payload,
)
from .images import prepare_image

# Transient failures we retry with backoff (matched by class name to stay
# robust across anthropic SDK versions).
_TRANSIENT_NAMES = {
    "RateLimitError",
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "APIStatusError",
    "OverloadedError",
}


def _is_transient(exc: Exception) -> bool:
    if type(exc).__name__ in _TRANSIENT_NAMES:
        return True
    return "overloaded" in str(exc).lower()


class AnthropicExtractor:
    def __init__(self, cfg: ExtractionConfig):
        self.cfg = cfg
        api_key = cfg.resolve_api_key()
        if not api_key:
            raise RuntimeError(
                "Anthropic API key not configured. Put it in credentials.toml "
                "([anthropic] api_key), set ANTHROPIC_API_KEY, or use the 'stub' "
                "provider."
            )
        # Imported here so the package imports cleanly without the SDK installed.
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)

    def _message(self, image_bytes: bytes, media_type: str):
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        return self._client.messages.create(
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
            system=SYSTEM_PROMPT,
            tools=[TOOL],
            tool_choice=TOOL_CHOICE,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                }
            ],
        )

    def extract(self, png_bytes: bytes, page_index: int) -> Sheet:
        image_bytes, media_type = prepare_image(png_bytes, self.cfg.model)

        attempts = max(1, self.cfg.max_retries)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                message = self._message(image_bytes, media_type)
                sheet = self._message_to_sheet(message, page_index)
                usage = getattr(message, "usage", None)
                if usage is not None:
                    sheet.input_tokens = getattr(usage, "input_tokens", None)
                    sheet.output_tokens = getattr(usage, "output_tokens", None)
                return sheet
            except ExtractionParseError as exc:
                # Re-ask once; the model occasionally omits the tool call.
                last_exc = exc
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_transient(exc) or attempt == attempts - 1:
                    raise
                time.sleep(min(2**attempt, 8))  # exponential backoff, capped
        assert last_exc is not None
        raise last_exc

    def _message_to_sheet(self, message, page_index: int) -> Sheet:
        # Preferred path: the forced tool call returns a typed input object.
        for block in message.content:
            if getattr(block, "type", None) == "tool_use":
                return parse_sheet_payload(block.input, page_index)
        # Fallback: some responses may carry JSON as text.
        text = "".join(
            getattr(b, "text", "") for b in message.content if getattr(b, "type", None) == "text"
        )
        if text.strip():
            return parse_sheet_json(text, page_index)
        raise ExtractionParseError("Claude returned no tool_use or text content.")

    def verify(self) -> tuple[bool, str]:
        try:
            self._client.messages.create(
                model=self.cfg.model,
                max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
        except Exception as exc:  # noqa: BLE001 - report any failure to the caller
            return False, f"{type(exc).__name__}: {exc}"
        return True, f"connected to Anthropic, model {self.cfg.model}"
