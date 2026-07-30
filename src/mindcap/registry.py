from __future__ import annotations

from mindcap.core.errors import InvalidSourceError
from mindcap.core.protocols import SourcePlugin


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, SourcePlugin] = {}

    def register(self, plugin: SourcePlugin) -> None:
        if plugin.source_type in self._plugins:
            raise ValueError(f'Plugin already registered: "{plugin.source_type}"')
        self._plugins[plugin.source_type] = plugin

    def get(self, source_type: str) -> SourcePlugin:
        try:
            return self._plugins[source_type]
        except KeyError as error:
            available = ", ".join(sorted(self._plugins)) or "none"
            raise InvalidSourceError(
                f'Unknown source type "{source_type}". Available: {available}'
            ) from error

    def names(self) -> list[str]:
        return sorted(self._plugins)


def create_default_registry() -> PluginRegistry:
    """Return a :class:`PluginRegistry` pre-populated with all built-in plugins.

    Prefer this function over :func:`build_registry` in new code.
    """
    return build_registry()


def build_registry() -> PluginRegistry:
    from mindcap.plugins.chatgpt.plugin import ChatGPTPlugin
    from mindcap.plugins.distrokid.plugin import DistroKidPlugin
    from mindcap.plugins.soundcloud.plugin import SoundCloudPlugin
    from mindcap.plugins.suno.plugin import SunoPlugin

    registry = PluginRegistry()
    registry.register(ChatGPTPlugin())
    registry.register(DistroKidPlugin())
    registry.register(SoundCloudPlugin())
    registry.register(SunoPlugin())
    return registry
