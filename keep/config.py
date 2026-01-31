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


CONFIG_FILENAME = "keep.toml"
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


def read_clawdbot_config() -> dict | None:
    """
    Read Clawdbot configuration if available.
    
    Checks:
    1. CLAWDBOT_CONFIG environment variable
    2. ~/.clawdbot/clawdbot.json (default location)
    
    Returns None if not found or invalid.
    """
    import json
    
    # Try environment variable first
    config_path_str = os.environ.get("CLAWDBOT_CONFIG")
    if config_path_str:
        config_file = Path(config_path_str)
    else:
        # Default location
        config_file = Path.home() / ".clawdbot" / "clawdbot.json"
    
    if not config_file.exists():
        return None
    
    try:
        with open(config_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def detect_default_providers() -> dict[str, ProviderConfig]:
    """
    Detect the best default providers for the current environment.
    
    Priority:
    1. Clawdbot integration (if configured and ANTHROPIC_API_KEY available)
    2. MLX (Apple Silicon local-first)
    3. OpenAI (if API key available)
    4. Fallback: sentence-transformers + passthrough/truncate
    
    Returns provider configs for: embedding, summarization, document
    """
    providers = {}
    
    # Check for Apple Silicon
    is_apple_silicon = (
        platform.system() == "Darwin" and 
        platform.machine() == "arm64"
    )
    
    # Check for API keys
    has_anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_openai_key = bool(
        os.environ.get("KEEP_OPENAI_API_KEY") or 
        os.environ.get("OPENAI_API_KEY")
    )
    
    # Check for Clawdbot config
    clawdbot_config = read_clawdbot_config()
    clawdbot_model = None
    if clawdbot_config:
        model_str = (clawdbot_config.get("agents", {})
                     .get("defaults", {})
                     .get("model", {})
                     .get("primary", ""))
        if model_str:
            clawdbot_model = model_str
    
    # Embedding: always local (sentence-transformers for compatibility, MLX optional)
    # Embeddings should stay local for privacy and cost
    if is_apple_silicon:
        # Prefer sentence-transformers even on M1 for stability
        providers["embedding"] = ProviderConfig("sentence-transformers")
    else:
        providers["embedding"] = ProviderConfig("sentence-transformers")
    
    # Summarization: priority order based on availability
    # 1. Clawdbot + Anthropic (if configured and key available)
    if clawdbot_model and clawdbot_model.startswith("anthropic/") and has_anthropic_key:
        # Extract model name from "anthropic/claude-sonnet-4-5" format
        model_name = clawdbot_model.split("/", 1)[1] if "/" in clawdbot_model else "claude-3-5-haiku-20241022"
        # Map Clawdbot model names to actual Anthropic model names
        model_mapping = {
            "claude-sonnet-4": "claude-sonnet-4-20250514",
            "claude-sonnet-4-5": "claude-sonnet-4-20250514",
            "claude-sonnet-3-5": "claude-3-5-sonnet-20241022",
            "claude-haiku-3-5": "claude-3-5-haiku-20241022",
        }
        actual_model = model_mapping.get(model_name, "claude-3-5-haiku-20241022")
        providers["summarization"] = ProviderConfig("anthropic", {"model": actual_model})
    # 2. MLX on Apple Silicon (local-first)
    elif is_apple_silicon:
        try:
            import mlx_lm  # noqa
            providers["summarization"] = ProviderConfig("mlx", {"model": "mlx-community/Llama-3.2-3B-Instruct-4bit"})
        except ImportError:
            if has_openai_key:
                providers["summarization"] = ProviderConfig("openai")
            else:
                providers["summarization"] = ProviderConfig("passthrough")
    # 3. OpenAI (if key available)
    elif has_openai_key:
        providers["summarization"] = ProviderConfig("openai")
    # 4. Fallback: truncate
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
