from pathlib import Path

from mindcap.core.models import CaptureRequest
from mindcap.plugins.chatgpt.plugin import ChatGPTPlugin
from mindcap.storage.filesystem import FilesystemStorageStrategy

IDENTIFIER = "6a14b69f-7834-83ea-8257-0eceadb41691"
FIXTURE = Path(__file__).parent / "fixtures" / "chatgpt" / "branching-conversation.json"


def _request(tmp_path: Path) -> CaptureRequest:
    return CaptureRequest(
        source_type="chatgpt",
        source=str(FIXTURE),
        provider="chatgpt",
        canonical_identifier=IDENTIFIER,
        canonical_url=f"https://chatgpt.com/c/{IDENTIFIER}",
        strategy="saved-json",
        artifact_root=tmp_path,
    )


def test_saved_json_pipeline_creates_verified_bundle(tmp_path: Path) -> None:
    plugin = ChatGPTPlugin()
    request = _request(tmp_path)
    envelope = plugin.strategy("saved-json").capture(request)
    normalized = plugin.normalize(envelope, IDENTIFIER)
    transcript = plugin.render(normalized)
    storage = FilesystemStorageStrategy()

    stored = storage.persist(request, envelope, normalized, transcript)

    assert stored.status == "complete"
    assert stored.version == 1
    assert (stored.path / "manifest.yaml").is_file()
    assert (stored.path / "normalized" / "conversation.json").is_file()
    rendered = (stored.path / "normalized" / "conversation.md").read_text()
    assert "selected path" in rendered
    assert "unselected alternate response" in rendered
    storage.verify(stored.path)


def test_unchanged_capture_reuses_version(tmp_path: Path) -> None:
    plugin = ChatGPTPlugin()
    request = _request(tmp_path)
    envelope = plugin.strategy("saved-json").capture(request)
    normalized = plugin.normalize(envelope, IDENTIFIER)
    transcript = plugin.render(normalized)
    storage = FilesystemStorageStrategy()

    first = storage.persist(request, envelope, normalized, transcript)
    second = storage.persist(request, envelope, normalized, transcript)

    assert first.version == 1
    assert second.version == 1
    assert second.status == "unchanged"
