"""Tests for media description protocol and integration."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from keep.providers.base import MediaDescriber, get_registry


# -----------------------------------------------------------------------------
# Protocol Tests
# -----------------------------------------------------------------------------

class TestMediaDescriberProtocol:
    """Verify MediaDescriber protocol compliance."""

    def test_protocol_is_runtime_checkable(self):
        """MediaDescriber protocol can be checked at runtime."""
        class FakeDescriber:
            def describe(self, path: str, content_type: str) -> str | None:
                return "a test description"

        assert isinstance(FakeDescriber(), MediaDescriber)

    def test_non_conforming_class_fails_check(self):
        """Classes without describe() don't satisfy the protocol."""
        class NotADescriber:
            def summarize(self, content: str) -> str:
                return content

        assert not isinstance(NotADescriber(), MediaDescriber)

    def test_none_for_unsupported_type(self):
        """Describers should return None for unsupported content types."""
        class ImageOnlyDescriber:
            def describe(self, path: str, content_type: str) -> str | None:
                if not content_type.startswith("image/"):
                    return None
                return "an image"

        d = ImageOnlyDescriber()
        assert d.describe("/test.mp3", "audio/mpeg") is None
        assert d.describe("/test.jpg", "image/jpeg") == "an image"


# -----------------------------------------------------------------------------
# Registry Tests
# -----------------------------------------------------------------------------

class TestMediaRegistry:
    """Test media provider registration and creation."""

    def test_register_and_create(self):
        """Can register and create a media describer."""
        registry = get_registry()

        class TestDescriber:
            def __init__(self, greeting="hello"):
                self.greeting = greeting

            def describe(self, path: str, content_type: str) -> str | None:
                return f"{self.greeting}: {path}"

        registry.register_media("test-media", TestDescriber)
        try:
            describer = registry.create_media("test-media", {"greeting": "hi"})
            assert describer.greeting == "hi"
            result = describer.describe("/img.jpg", "image/jpeg")
            assert result == "hi: /img.jpg"
        finally:
            # Clean up registration
            del registry._media_providers["test-media"]

    def test_create_unknown_raises(self):
        """Creating unknown media provider raises ValueError."""
        registry = get_registry()
        with pytest.raises(ValueError, match="Unknown media describer"):
            registry.create_media("nonexistent-provider")


# -----------------------------------------------------------------------------
# Config Tests
# -----------------------------------------------------------------------------

class TestMediaConfig:
    """Test media section in config load/save/detect."""

    def test_config_media_defaults_to_none(self, tmp_path):
        """StoreConfig.media defaults to None."""
        from keep.config import StoreConfig
        config = StoreConfig(path=tmp_path)
        assert config.media is None

    def test_config_roundtrip_with_media(self, tmp_path):
        """Media config survives save + load cycle."""
        from keep.config import StoreConfig, ProviderConfig, save_config, load_config

        config = StoreConfig(
            path=tmp_path,
            config_dir=tmp_path,
            media=ProviderConfig("mlx", {
                "vision_model": "test-vision",
                "whisper_model": "test-whisper",
            }),
        )
        save_config(config)
        loaded = load_config(tmp_path)

        assert loaded.media is not None
        assert loaded.media.name == "mlx"
        assert loaded.media.params["vision_model"] == "test-vision"
        assert loaded.media.params["whisper_model"] == "test-whisper"

    def test_config_roundtrip_without_media(self, tmp_path):
        """Config without media section loads as None (backward compatible)."""
        from keep.config import StoreConfig, save_config, load_config

        config = StoreConfig(path=tmp_path, config_dir=tmp_path, media=None)
        save_config(config)
        loaded = load_config(tmp_path)

        assert loaded.media is None

    def test_detect_providers_includes_media_key(self):
        """detect_default_providers() returns a 'media' key."""
        from keep.config import detect_default_providers
        providers = detect_default_providers()
        assert "media" in providers
        # media may be None if no vision/whisper libraries installed


# -----------------------------------------------------------------------------
# LockedMediaDescriber Tests
# -----------------------------------------------------------------------------

class TestLockedMediaDescriber:
    """Test LockedMediaDescriber wrapper."""

    def test_locked_describer_delegates(self, tmp_path):
        """LockedMediaDescriber delegates describe() calls."""
        from keep.model_lock import LockedMediaDescriber

        inner = MagicMock()
        inner.describe.return_value = "a cat"
        locked = LockedMediaDescriber(inner, tmp_path / ".media.lock")

        result = locked.describe("/test.jpg", "image/jpeg")
        assert result == "a cat"
        inner.describe.assert_called_once_with("/test.jpg", "image/jpeg")

    def test_locked_describer_release(self, tmp_path):
        """LockedMediaDescriber.release() cleans up."""
        from keep.model_lock import LockedMediaDescriber

        inner = MagicMock()
        locked = LockedMediaDescriber(inner, tmp_path / ".media.lock")
        locked.release()
        assert locked._provider is None


# -----------------------------------------------------------------------------
# Keeper Integration Tests (with mocks)
# -----------------------------------------------------------------------------

def _make_mock_doc(uri, content, content_type, tags=None):
    """Create a mock Document for testing."""
    mock_doc = MagicMock()
    mock_doc.uri = uri
    mock_doc.content = content
    mock_doc.content_type = content_type
    mock_doc.metadata = None
    mock_doc.tags = tags
    return mock_doc


def _keeper_skip_migration(kp):
    """Skip system docs migration for test Keepers."""
    from keep.config import SYSTEM_DOCS_VERSION
    kp._config.system_docs_version = SYSTEM_DOCS_VERSION
    kp._needs_sysdoc_migration = False


class TestMediaIntegration:
    """Test media description integration in Keeper.update()."""

    def test_update_image_appends_description(self, mock_providers, tmp_path):
        """When media describer is configured, image update appends description."""
        from keep.api import Keeper

        mock_doc = _make_mock_doc(
            "file:///test.jpg",
            "Dimensions: 1920x1080\nCamera: Canon EOS R5",
            "image/jpeg",
            tags={"dimensions": "1920x1080"},
        )
        mock_providers["document"].fetch = lambda uri: mock_doc

        mock_describer = MagicMock()
        mock_describer.describe.return_value = "A sunset over mountains"

        kp = Keeper(store_path=tmp_path)
        _keeper_skip_migration(kp)
        kp._media_describer = mock_describer

        item = kp.update("file:///test.jpg")

        assert "A sunset over mountains" in item.summary
        assert "Canon EOS R5" in item.summary
        mock_describer.describe.assert_called_once()
        kp.close()

    def test_update_without_media_provider(self, mock_providers, tmp_path):
        """Without media provider, image gets metadata-only content."""
        from keep.api import Keeper

        mock_doc = _make_mock_doc(
            "file:///test.jpg", "Dimensions: 1920x1080", "image/jpeg",
        )
        mock_providers["document"].fetch = lambda uri: mock_doc

        kp = Keeper(store_path=tmp_path)
        _keeper_skip_migration(kp)
        assert kp._get_media_describer() is None

        item = kp.update("file:///test.jpg")
        assert "Dimensions: 1920x1080" in item.summary
        kp.close()

    def test_update_media_failure_graceful(self, mock_providers, tmp_path):
        """Media description failure doesn't block indexing."""
        from keep.api import Keeper

        mock_doc = _make_mock_doc(
            "file:///test.jpg", "Dimensions: 100x100", "image/jpeg",
        )
        mock_providers["document"].fetch = lambda uri: mock_doc

        mock_describer = MagicMock()
        mock_describer.describe.side_effect = RuntimeError("model crashed")

        kp = Keeper(store_path=tmp_path)
        _keeper_skip_migration(kp)
        kp._media_describer = mock_describer

        item = kp.update("file:///test.jpg")
        assert item is not None
        assert "Dimensions: 100x100" in item.summary
        kp.close()

    def test_text_files_skip_media_description(self, mock_providers, tmp_path):
        """Text files never trigger media description."""
        from keep.api import Keeper

        mock_doc = _make_mock_doc(
            "file:///test.md", "# Hello World", "text/markdown",
        )
        mock_providers["document"].fetch = lambda uri: mock_doc

        mock_describer = MagicMock()
        mock_describer.describe.return_value = "should not be called"

        kp = Keeper(store_path=tmp_path)
        _keeper_skip_migration(kp)
        kp._media_describer = mock_describer

        kp.update("file:///test.md")
        mock_describer.describe.assert_not_called()
        kp.close()

    def test_audio_content_triggers_description(self, mock_providers, tmp_path):
        """Audio files trigger media description."""
        from keep.api import Keeper

        mock_doc = _make_mock_doc(
            "file:///test.mp3", "Title: Song\nArtist: Band", "audio/mpeg",
            tags={"title": "Song", "artist": "Band"},
        )
        mock_providers["document"].fetch = lambda uri: mock_doc

        mock_describer = MagicMock()
        mock_describer.describe.return_value = "A rock song with guitar solo"

        kp = Keeper(store_path=tmp_path)
        _keeper_skip_migration(kp)
        kp._media_describer = mock_describer

        item = kp.update("file:///test.mp3")
        assert "A rock song with guitar solo" in item.summary
        assert "Title: Song" in item.summary
        mock_describer.describe.assert_called_once_with(
            "/test.mp3", "audio/mpeg"
        )
        kp.close()

    def test_description_none_skips_enrichment(self, mock_providers, tmp_path):
        """When describer returns None, content is not modified."""
        from keep.api import Keeper

        mock_doc = _make_mock_doc(
            "file:///test.jpg", "Dimensions: 100x100", "image/jpeg",
        )
        mock_providers["document"].fetch = lambda uri: mock_doc

        mock_describer = MagicMock()
        mock_describer.describe.return_value = None

        kp = Keeper(store_path=tmp_path)
        _keeper_skip_migration(kp)
        kp._media_describer = mock_describer

        item = kp.update("file:///test.jpg")
        assert "Description:" not in item.summary
        kp.close()


# -----------------------------------------------------------------------------
# MLX Describer Unit Tests (no real models)
# -----------------------------------------------------------------------------

class TestMLXMediaDescriber:
    """Test MLXMediaDescriber composite without real models."""

    def test_returns_none_for_text(self):
        """MLXMediaDescriber returns None for text content types."""
        from keep.providers.mlx import MLXMediaDescriber

        # Patch the sub-provider imports to avoid ImportError
        describer = object.__new__(MLXMediaDescriber)
        describer._vision = None
        describer._whisper = None
        describer._vision_checked = True
        describer._whisper_checked = True

        assert describer.describe("/test.txt", "text/plain") is None

    def test_image_delegates_to_vision(self):
        """MLXMediaDescriber delegates images to vision sub-provider."""
        from keep.providers.mlx import MLXMediaDescriber

        describer = object.__new__(MLXMediaDescriber)
        mock_vision = MagicMock()
        mock_vision.describe.return_value = "A cat"
        describer._vision = mock_vision
        describer._vision_checked = True
        describer._whisper = None
        describer._whisper_checked = True

        result = describer.describe("/cat.jpg", "image/jpeg")
        assert result == "A cat"
        mock_vision.describe.assert_called_once_with("/cat.jpg", "image/jpeg")

    def test_audio_delegates_to_whisper(self):
        """MLXMediaDescriber delegates audio to whisper sub-provider."""
        from keep.providers.mlx import MLXMediaDescriber

        describer = object.__new__(MLXMediaDescriber)
        describer._vision = None
        describer._vision_checked = True
        mock_whisper = MagicMock()
        mock_whisper.describe.return_value = "Hello world"
        describer._whisper = mock_whisper
        describer._whisper_checked = True

        result = describer.describe("/speech.mp3", "audio/mpeg")
        assert result == "Hello world"
        mock_whisper.describe.assert_called_once_with("/speech.mp3", "audio/mpeg")
