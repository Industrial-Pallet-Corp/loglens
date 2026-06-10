"""Vision-LLM extraction behind a swappable provider interface."""

from __future__ import annotations

from ..config import ExtractionConfig
from .base import Extractor
from .stub import StubExtractor


def build_extractor(cfg: ExtractionConfig) -> Extractor:
    """Factory selecting an extractor implementation from config."""

    provider = cfg.provider.lower()
    if provider == "stub":
        return StubExtractor()
    if provider == "anthropic":
        # Imported lazily so the stub path works without the SDK/key present.
        from .anthropic_provider import AnthropicExtractor

        return AnthropicExtractor(cfg)
    raise ValueError(f"Unknown extraction provider: {cfg.provider!r}")


__all__ = ["Extractor", "StubExtractor", "build_extractor"]
