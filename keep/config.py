"""
Configuration management for reflective memory stores.

The configuration is stored as a TOML file in the store directory.
It specifies which providers to use and their parameters.
"""

import importlib.resources
import os
import platform
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# tomli_w for writing TOML (tomllib is read-only)
import tomli_w


CONFIG_FILENAME = "keep.toml"
CONFIG_VERSION = 3  # Bumped for document versioning support
SYSTEM_DOCS_VERSION = 8  # Increment when bundled system docs content changes


def get_tool_directory() -> Path:
    """
    Return keep package directory (contains SKILL.md and docs/library/).

    For installed package: the keep/ package directory itself (SKILL.md is inside).
    For development: the repository root (one level up from keep/).
    """
    keep_pkg = importlib.resources.files("keep")
    pkg_path = Path(str(keep_pkg))

    # Check if SKILL.md is in the package (installed via wheel with force-include)
    if (pkg_path / "SKILL.md").exists():
        return pkg_path

    # Development: SKILL.md is at repo root (parent of keep/)
    if (pkg_path.parent / "SKILL.md").exists():
        return pkg_path.parent

    # Fallback: return the package directory
    return pkg_path


@dataclass
class ProviderConfig:
    """Configuration for a single provider."""
    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbeddingIdentity:
    """
    Identity of an embedding model for compatibility checking.
    
    Two embeddings are compatible only if they have the same identity.
    Different models, even with the same dimension, produce incompatible vectors.
    """
    provider: str  # e.g., "sentence-transformers", "openai"
    model: str     # e.g., "all-MiniLM-L6-v2", "text-embedding-3-small"
    dimension: int # e.g., 384, 1536
    
    @property
    def key(self) -> str:
        """
        Short key for collection naming.
        
        Format: {provider}_{model_slug}
        e.g., "st_MiniLM_L6_v2", "openai_3_small"
        """
        # Simplify model name for use in collection names
        model_slug = self.model.replace("-", "_").replace(".", "_")
        # Remove common prefixes
        for prefix in ["all_", "text_embedding_"]:
            if model_slug.lower().startswith(prefix):
                model_slug = model_slug[len(prefix):]
        # Shorten provider names
        provider_short = {
            "sentence-transformers": "st",
            "openai": "openai",
            "gemini": "gemini",
            "ollama": "ollama",
            "voyage": "voyage",
        }.get(self.provider, self.provider[:6])
        
        return f"{provider_short}_{model_slug}"


@dataclass
class StoreConfig:
    """Complete store configuration."""
    path: Path  # Store path (where data lives)
    config_dir: Optional[Path] = None  # Where config was loaded from (may differ from path)
    store_path: Optional[str] = None  # Explicit store.path from config file (raw string)
    version: int = CONFIG_VERSION
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Provider configurations
    embedding: ProviderConfig = field(default_factory=lambda: ProviderConfig("sentence-transformers"))
    summarization: ProviderConfig = field(default_factory=lambda: ProviderConfig("truncate"))
    document: ProviderConfig = field(default_factory=lambda: ProviderConfig("composite"))

    # Embedding identity (set after first use, used for validation)
    embedding_identity: Optional[EmbeddingIdentity] = None

    # Default tags applied to all update/remember operations
    default_tags: dict[str, str] = field(default_factory=dict)

    # Maximum length for summaries (used for smart remember and validation)
    max_summary_length: int = 2000

    # System docs version (tracks which bundled docs have been applied to this store)
    system_docs_version: int = 0

    # Tool integrations tracking (presence of key = handled, value = installed or skipped)
    integrations: dict[str, Any] = field(default_factory=dict)

    @property
    def config_path(self) -> Path:
        """Path to the TOML config file."""
        config_location = self.config_dir if self.config_dir else self.path
        return config_location / CONFIG_FILENAME

    def exists(self) -> bool:
        """Check if config file exists."""
        return self.config_path.exists()


def read_openclaw_config() -> dict | None:
    """
    Read OpenClaw configuration if available.

    Checks:
    1. OPENCLAW_CONFIG environment variable
    2. ~/.openclaw/openclaw.json (default location)

    Returns None if not found or invalid.
    """
    import json

    # Try environment variable first
    config_path_str = os.environ.get("OPENCLAW_CONFIG")
    if config_path_str:
        config_file = Path(config_path_str)
    else:
        # Default location
        config_file = Path.home() / ".openclaw" / "openclaw.json"

    if not config_file.exists():
        return None

    try:
        with open(config_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def get_openclaw_memory_search_config(openclaw_config: dict | None) -> dict | None:
    """
    Extract memorySearch config from OpenClaw config.

    Returns the memorySearch settings or None if not configured.

    Example structure:
        {
            "provider": "openai" | "gemini" | "local" | "auto",
            "model": "text-embedding-3-small",
            "remote": {
                "apiKey": "sk-...",
                "baseUrl": "https://..."
            }
        }
    """
    if not openclaw_config:
        return None

    return (openclaw_config
            .get("agents", {})
            .get("defaults", {})
            .get("memorySearch", None))


def _detect_ollama() -> dict | None:
    """
    Check if Ollama is running locally and discover available models.

    Respects OLLAMA_HOST environment variable (default: http://localhost:11434).
    Uses a short timeout (0.5s) to avoid blocking during provider detection.

    Returns dict with 'base_url' and 'models' if Ollama is reachable
    with at least one model, None otherwise.
    """
    import json
    import urllib.request

    base_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not base_url.startswith("http"):
        base_url = f"http://{base_url}"

    try:
        req = urllib.request.Request(f"{base_url}/api/tags")
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            if models:
                return {"base_url": base_url, "models": models}
    except Exception:
        pass
    return None


def _ollama_pick_models(models: list[str]) -> tuple[str, str | None]:
    """
    Choose the best Ollama models for embeddings and summarization.

    Returns (embed_model, chat_model). chat_model is None if only
    embedding-specific models are available.
    """
    # Separate embedding-specific models from generative models
    embed_models = []
    generative_models = []
    for m in models:
        base = m.split(":")[0]
        if "embed" in base:
            embed_models.append(m)
        else:
            generative_models.append(m)

    # For embeddings: prefer dedicated embedding model, else first available
    embed_model = embed_models[0] if embed_models else models[0]

    # For summarization: need a generative model (embedding models can't generate text)
    chat_model = generative_models[0] if generative_models else None

    return embed_model, chat_model


def detect_default_providers() -> dict[str, ProviderConfig | None]:
    """
    Detect the best default providers for the current environment.

    Priority for embeddings:
    1. API keys: VOYAGE_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY
    2. Ollama (if running locally with models)
    3. Local: MLX (Apple Silicon), sentence-transformers
    4. None if nothing available

    Priority for summarization:
    1. API keys: ANTHROPIC_API_KEY (or CLAUDE_CODE_OAUTH_TOKEN), OPENAI_API_KEY, GEMINI_API_KEY
    2. Ollama (if running locally with a generative model)
    3. Local: MLX (Apple Silicon)
    4. Fallback: truncate (always available)

    Returns provider configs for: embedding, summarization, document.
    embedding may be None if no provider is available.
    """
    providers: dict[str, ProviderConfig | None] = {}

    # Check for Apple Silicon
    is_apple_silicon = (
        platform.system() == "Darwin" and
        platform.machine() == "arm64"
    )

    # Check for API keys
    has_anthropic_key = bool(
        os.environ.get("ANTHROPIC_API_KEY") or
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    )
    has_openai_key = bool(
        os.environ.get("KEEP_OPENAI_API_KEY") or
        os.environ.get("OPENAI_API_KEY")
    )
    has_gemini_key = bool(
        os.environ.get("GEMINI_API_KEY") or
        os.environ.get("GOOGLE_API_KEY")
    )
    has_voyage_key = bool(os.environ.get("VOYAGE_API_KEY"))

    # Check for Ollama (lazy — only probed when no API key covers both)
    _ollama_info: dict | None = None
    _ollama_checked = False

    def get_ollama() -> dict | None:
        nonlocal _ollama_info, _ollama_checked
        if not _ollama_checked:
            _ollama_checked = True
            _ollama_info = _detect_ollama()
        return _ollama_info

    # --- Embedding provider ---
    # Priority: Voyage > OpenAI > Gemini > Ollama > MLX > sentence-transformers
    embedding_provider: ProviderConfig | None = None

    # 1. API providers first (Voyage uses direct REST, no SDK import needed)
    if has_voyage_key:
        embedding_provider = ProviderConfig("voyage", {"model": "voyage-3.5-lite"})
    elif has_openai_key:
        embedding_provider = ProviderConfig("openai")
    elif has_gemini_key:
        embedding_provider = ProviderConfig("gemini")

    # 2. Ollama (local server, no API key needed)
    if embedding_provider is None:
        ollama = get_ollama()
        if ollama:
            embed_model, _ = _ollama_pick_models(ollama["models"])
            params: dict[str, Any] = {"model": embed_model}
            if ollama["base_url"] != "http://localhost:11434":
                params["base_url"] = ollama["base_url"]
            embedding_provider = ProviderConfig("ollama", params)

    # 3. Local providers (MLX, sentence-transformers)
    if embedding_provider is None:
        if is_apple_silicon:
            try:
                import mlx.core  # noqa
                embedding_provider = ProviderConfig("mlx", {"model": "all-MiniLM-L6-v2"})
            except ImportError:
                pass

        if embedding_provider is None:
            try:
                import sentence_transformers  # noqa
                embedding_provider = ProviderConfig("sentence-transformers")
            except ImportError:
                pass

    # May be None - CLI will show helpful error
    providers["embedding"] = embedding_provider

    # --- Summarization provider ---
    # Priority: Anthropic > OpenAI > Gemini > Ollama > MLX > truncate
    summarization_provider: ProviderConfig | None = None

    # 1. API providers
    if has_anthropic_key:
        summarization_provider = ProviderConfig("anthropic", {"model": "claude-3-haiku-20240307"})
    elif has_openai_key:
        summarization_provider = ProviderConfig("openai")
    elif has_gemini_key:
        summarization_provider = ProviderConfig("gemini")

    # 2. Ollama (needs a generative model, not embedding-only)
    if summarization_provider is None:
        ollama = get_ollama()
        if ollama:
            _, chat_model = _ollama_pick_models(ollama["models"])
            if chat_model:
                params = {"model": chat_model}
                if ollama["base_url"] != "http://localhost:11434":
                    params["base_url"] = ollama["base_url"]
                summarization_provider = ProviderConfig("ollama", params)

    # 3. Local MLX (Apple Silicon)
    if summarization_provider is None and is_apple_silicon:
        try:
            import mlx_lm  # noqa
            summarization_provider = ProviderConfig("mlx", {"model": "mlx-community/Llama-3.2-3B-Instruct-4bit"})
        except ImportError:
            pass

    # 4. Fallback: truncate (always available)
    if summarization_provider is None:
        summarization_provider = ProviderConfig("truncate")

    providers["summarization"] = summarization_provider

    # Document provider is always composite
    providers["document"] = ProviderConfig("composite")

    return providers


def create_default_config(config_dir: Path, store_path: Optional[Path] = None) -> StoreConfig:
    """
    Create a new config with auto-detected defaults.

    Args:
        config_dir: Directory where keep.toml will be saved
        store_path: Optional explicit store location (if different from config_dir)
    """
    providers = detect_default_providers()

    # If store_path is provided and different from config_dir, record it
    store_path_str = None
    actual_store = config_dir
    if store_path and store_path.resolve() != config_dir.resolve():
        store_path_str = str(store_path)
        actual_store = store_path

    return StoreConfig(
        path=actual_store,
        config_dir=config_dir,
        store_path=store_path_str,
        embedding=providers["embedding"],
        summarization=providers["summarization"],
        document=providers["document"],
    )


def load_config(config_dir: Path) -> StoreConfig:
    """
    Load configuration from a config directory.

    The config_dir is where keep.toml lives. The actual store location
    may be different if store.path is set in the config.

    Args:
        config_dir: Directory containing keep.toml

    Raises:
        FileNotFoundError: If config doesn't exist
        ValueError: If config is invalid
    """
    config_path = config_dir / CONFIG_FILENAME

    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    # Validate version
    version = data.get("store", {}).get("version", 1)
    if version > CONFIG_VERSION:
        raise ValueError(f"Config version {version} is newer than supported ({CONFIG_VERSION})")

    # Parse store.path - explicit store location
    store_path_str = data.get("store", {}).get("path")
    if store_path_str:
        actual_store = Path(store_path_str).expanduser().resolve()
    else:
        actual_store = config_dir  # Backwards compat: store is at config location

    # Parse provider configs
    def parse_provider(section: dict) -> ProviderConfig:
        return ProviderConfig(
            name=section.get("name", ""),
            params={k: v for k, v in section.items() if k != "name"},
        )

    # Parse default tags (filter out system tags)
    raw_tags = data.get("tags", {})
    default_tags = {k: str(v) for k, v in raw_tags.items()
                    if not k.startswith("_")}

    # Parse max_summary_length (default 2000)
    max_summary_length = data.get("store", {}).get("max_summary_length", 2000)

    # Parse system_docs_version (default 0 for stores that predate this feature)
    system_docs_version = data.get("store", {}).get("system_docs_version", 0)

    # Parse integrations section (presence = handled)
    integrations = data.get("integrations", {})

    return StoreConfig(
        path=actual_store,
        config_dir=config_dir,
        store_path=store_path_str,
        version=version,
        created=data.get("store", {}).get("created", ""),
        embedding=parse_provider(data.get("embedding", {"name": "sentence-transformers"})),
        summarization=parse_provider(data.get("summarization", {"name": "truncate"})),
        document=parse_provider(data.get("document", {"name": "composite"})),
        embedding_identity=parse_embedding_identity(data.get("embedding_identity")),
        default_tags=default_tags,
        max_summary_length=max_summary_length,
        system_docs_version=system_docs_version,
        integrations=integrations,
    )


def parse_embedding_identity(data: dict | None) -> EmbeddingIdentity | None:
    """Parse embedding identity from config data."""
    if data is None:
        return None
    provider = data.get("provider")
    model = data.get("model")
    dimension = data.get("dimension")
    if provider and model and dimension:
        return EmbeddingIdentity(provider=provider, model=model, dimension=dimension)
    return None


def save_config(config: StoreConfig) -> None:
    """
    Save configuration to the config directory.

    Creates the directory if it doesn't exist.
    """
    # Ensure config directory exists
    config_location = config.config_dir if config.config_dir else config.path
    config_location.mkdir(parents=True, exist_ok=True)

    # Build TOML structure
    def provider_to_dict(p: ProviderConfig) -> dict:
        d = {"name": p.name}
        d.update(p.params)
        return d

    store_section: dict[str, Any] = {
        "version": config.version,
        "created": config.created,
    }
    # Only write store.path if explicitly set (not default)
    if config.store_path:
        store_section["path"] = config.store_path
    # Only write max_summary_length if not default
    if config.max_summary_length != 2000:
        store_section["max_summary_length"] = config.max_summary_length
    # Write system_docs_version if set (tracks migration state)
    if config.system_docs_version > 0:
        store_section["system_docs_version"] = config.system_docs_version

    data: dict[str, Any] = {
        "store": store_section,
    }

    # Only include providers if they're configured
    if config.embedding:
        data["embedding"] = provider_to_dict(config.embedding)
    if config.summarization:
        data["summarization"] = provider_to_dict(config.summarization)
    if config.document:
        data["document"] = provider_to_dict(config.document)

    # Add embedding identity if set
    if config.embedding_identity:
        data["embedding_identity"] = {
            "provider": config.embedding_identity.provider,
            "model": config.embedding_identity.model,
            "dimension": config.embedding_identity.dimension,
        }

    # Add default tags if set
    if config.default_tags:
        data["tags"] = config.default_tags

    # Add integrations tracking if set
    if config.integrations:
        data["integrations"] = config.integrations

    with open(config.config_path, "wb") as f:
        tomli_w.dump(data, f)


def load_or_create_config(config_dir: Path, store_path: Optional[Path] = None) -> StoreConfig:
    """
    Load existing config or create a new one with defaults.

    This is the main entry point for config management.

    Args:
        config_dir: Directory containing (or to contain) keep.toml
        store_path: Optional explicit store location (for new configs only)
    """
    config_path = config_dir / CONFIG_FILENAME

    if config_path.exists():
        return load_config(config_dir)
    else:
        config = create_default_config(config_dir, store_path)
        save_config(config)
        return config
