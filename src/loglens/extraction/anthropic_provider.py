"""Anthropic Claude vision extractor."""

from __future__ import annotations

import base64

from ..config import ExtractionConfig
from ..models import Sheet
from .base import SYSTEM_PROMPT, USER_PROMPT, parse_sheet_json


class AnthropicExtractor:
    def __init__(self, cfg: ExtractionConfig):
        self.cfg = cfg
        api_key = cfg.resolve_api_key()
        if not api_key:
            raise RuntimeError(
                "Anthropic API key not configured. Set ANTHROPIC_API_KEY or "
                "extraction.api_key in config, or use the 'stub' provider."
            )
        # Imported here so the package imports cleanly without the SDK installed.
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)

    def extract(self, png_bytes: bytes, page_index: int) -> Sheet:
        b64 = base64.standard_b64encode(png_bytes).decode("ascii")
        message = self._client.messages.create(
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                }
            ],
        )
        text = "".join(
            block.text for block in message.content if block.type == "text"
        )
        return parse_sheet_json(text, page_index)
