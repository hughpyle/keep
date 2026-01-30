"""
Configuration management for associative memory stores.

The configuration is stored as a TOML file in the store directory.
It specifies which providers to use and their parameters.
"""

import os
import platform
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# tomli_w for writing TOML (tomllib is read-only)
try:
    import tomli_w
except ImportError:
    tomli_w = None  # type: ignore


CONFIG_FILENAME = "assocmem.toml"
CONFIG_VERSION = 1


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""
    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoreConfig:
    """Complete store configuration."""
    path: Path
    version: int = CONFIG_VERSION
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    # Provider configurations
    embedding: ProviderConfig = field(default_factory=lambda: ProviderConfig("sentence-transformers"))
    summarization: ProviderConfig = field(default_factory=lambda: ProviderConfig("truncate"))
    document: ProviderConfig = field(default_factory=lambda: ProviderConfig("composite"))
    
    @property
    def config_path(self) -> Path:
        """Path to the TOML config file."""
        return self.path / CONFIG_FILENAME
    
    def exists(self) -> bool:
        """Check if config file exists."""
        return self.config_path.exists()


def detect_default_providers() -> dict[str, ProviderConfig]:
    """
    Detect the best default providers for the current environment.
    
    Returns provider configs for: embedding, summarization, tagging
    """
    providers = {}
    
    # Check for Apple Silicon
    is_apple_silicon = (
        platform.system() == "Darwin" and 
        platform.machine() == "arm64"
    )
    
    # Check for OpenAI API key
    has_openai_key = bool(
        os.environ.get("ASSOCMEM_OPENAI_API_KEY") or 
        os.environ.get("OPENAI_API_KEY")
    )
    
    # Embedding: prefer sentence-transformers for maximum compatibility
    # MLX is available but requires model downloads which can be slow/require auth
    providers["embedding"] = ProviderConfig("sentence-transformers")
    
    # Summarization: prefer MLX on Apple Silicon, then OpenAI if key available
    if is_apple_silicon:
        try:
            import mlx_lm  # noqa
            providers["summarization"] = ProviderConfig("mlx", {"model": "mlx-community/Llama-3.2-3B-Instruct-4bit"})
        except ImportError:
            if has_openai_key:
                providers["summarization"] = ProviderConfig("openai")
            else:
                providers["summarization"] = ProviderConfig("passthrough")
    elif has_openai_key:
        providers["summarization"] = ProviderConfig("openai")
    else:
        providers["summarization"] = ProviderConfig("truncate")
    
    # Document provider is always composite
    providers["document"] = ProviderConfig("composite")
    
    return providers


def create_default_config(store_path: Path) -> StoreConfig:
    """Create a new config with auto-detected defaults."""
    providers = detect_default_providers()
    
    return StoreConfig(
        path=store_path,
        embedding=providers["embedding"],
        summarization=providers["summarization"],
        document=providers["document"],
    )


def load_config(store_path: Path) -> StoreConfig:
    """
    Load configuration from a store directory.
    
    Raises:
        FileNotFoundError: If config doesn't exist
        ValueError: If config is invalid
    """
    config_path = store_path / CONFIG_FILENAME
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    
    # Validate version
    version = data.get("store", {}).get("version", 1)
    if version > CONFIG_VERSION:
        raise ValueError(f"Config version {version} is newer than supported ({CONFIG_VERSION})")
    
    # Parse provider configs
    def parse_provider(section: dict) -> ProviderConfig:
        return ProviderConfig(
            name=section.get("name", ""),
            params={k: v for k, v in section.items() if k != "name"},
        )
    
    return StoreConfig(
        path=store_path,
        version=version,
        created=data.get("store", {}).get("created", ""),
        embedding=parse_provider(data.get("embedding", {"name": "sentence-transformers"})),
        summarization=parse_provider(data.get("summarization", {"name": "truncate"})),
        document=parse_provider(data.get("document", {"name": "composite"})),
    )


def save_config(config: StoreConfig) -> None:
    """
    Save configuration to the store directory.
    
    Creates the directory if it doesn't exist.
    """
    if tomli_w is None:
        raise RuntimeError("tomli_w is required to save config. Install with: pip install tomli-w")
    
    # Ensure directory exists
    config.path.mkdir(parents=True, exist_ok=True)
    
    # Build TOML structure
    def provider_to_dict(p: ProviderConfig) -> dict:
        d = {"name": p.name}
        d.update(p.params)
        return d
    
    data = {
        "store": {
            "version": config.version,
            "created": config.created,
        },
        "embedding": provider_to_dict(config.embedding),
        "summarization": provider_to_dict(config.summarization),
        "document": provider_to_dict(config.document),
    }
    
    with open(config.config_path, "wb") as f:
        tomli_w.dump(data, f)


def load_or_create_config(store_path: Path) -> StoreConfig:
    """
    Load existing config or create a new one with defaults.
    
    This is the main entry point for config management.
    """
    config_path = store_path / CONFIG_FILENAME
    
    if config_path.exists():
        return load_config(store_path)
    else:
        config = create_default_config(store_path)
        save_config(config)
        return config
