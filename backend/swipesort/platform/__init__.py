"""OS abstraction layer. The only package allowed to inspect sys.platform."""

from __future__ import annotations

import sys
from functools import lru_cache

from swipesort.platform.base import AccessReport, Platform, SourceMetadata

__all__ = ["AccessReport", "Platform", "SourceMetadata", "get_platform"]


@lru_cache(maxsize=1)
def get_platform() -> Platform:
    """Return the process-wide Platform implementation for this OS."""
    if sys.platform == "darwin":
        from swipesort.platform.macos import MacOSPlatform

        return MacOSPlatform()
    if sys.platform == "win32":
        from swipesort.platform.windows import WindowsPlatform

        return WindowsPlatform()
    from swipesort.platform.fallback import FallbackPlatform

    return FallbackPlatform()
