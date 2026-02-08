"""
Embedding providers for generating vector representations of text.
"""

import os
from typing import Any

from .base import EmbeddingProvider, get_registry


class SentenceTransformerEmbedding:
    """
    Embedding provider using sentence-transformers library.

    Runs locally, no API key required. Good default for getting started.

    Requires: pip install sentence-transformers
    """

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        """
        Args:
            model: Model name from sentence-transformers hub
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError(
                "SentenceTransformerEmbedding requires 'sentence-transformers' library. "
                "Install with: pip install sentence-transformers"
            )

        self.model_name = model

        # Check if model is already cached locally to avoid network calls
        # Expand short model names (e.g. "all-MiniLM-L6-v2" -> "sentence-transformers/all-MiniLM-L6-v2")
        local_only = False
        try:
            from huggingface_hub import try_to_load_from_cache
            repo_id = model if "/" in model else f"sentence-transformers/{model}"
            cached = try_to_load_from_cache(repo_id, "config.json")
            local_only = cached is not None
        except ImportError:
            pass

        self._model = SentenceTransformer(model, local_files_only=local_only)
    
    @property
    def dimension(self) -> int:
        """Get embedding dimension from the model."""
        return self._model.get_sentence_embedding_dimension()
    
    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        embedding = self._model.encode(text, convert_to_numpy=True)
        return embedding.tolist()
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        embeddings = self._model.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()


class OpenAIEmbedding:
    """
    Embedding provider using OpenAI's API.
    
    Requires: KEEP_OPENAI_API_KEY or OPENAI_API_KEY environment variable.
    Requires: pip install openai
    """
    
    # Model dimensions (as of 2024)
    MODEL_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }
    
    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
    ):
        """
        Args:
            model: OpenAI embedding model name
            api_key: API key (defaults to environment variable)
        """
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError(
                "OpenAIEmbedding requires 'openai' library. "
                "Install with: pip install openai"
            )
        
        self.model_name = model
        # Use lookup table if available, otherwise detect lazily from first embedding
        self._dimension = self.MODEL_DIMENSIONS.get(model)

        # Resolve API key
        key = api_key or os.environ.get("KEEP_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError(
                "OpenAI API key required. Set KEEP_OPENAI_API_KEY or OPENAI_API_KEY"
            )
        
        self._client = OpenAI(api_key=key)
    
    @property
    def dimension(self) -> int:
        """Get embedding dimension for the model (detected lazily if unknown)."""
        if self._dimension is None:
            # Unknown model: detect from first embedding
            test_embedding = self.embed("dimension test")
            self._dimension = len(test_embedding)
        return self._dimension

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        response = self._client.embeddings.create(
            model=self.model_name,
            input=text,
        )
        embedding = response.data[0].embedding
        # Cache dimension if not yet known
        if self._dimension is None:
            self._dimension = len(embedding)
        return embedding
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        response = self._client.embeddings.create(
            model=self.model_name,
            input=texts,
        )
        # Sort by index to ensure order matches input
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [d.embedding for d in sorted_data]


class GeminiEmbedding:
    """
    Embedding provider using Google's Gemini API.

    Requires: GEMINI_API_KEY or GOOGLE_API_KEY environment variable.
    Requires: pip install google-genai
    """

    # Model dimensions (as of 2025)
    MODEL_DIMENSIONS = {
        "text-embedding-004": 768,
        "embedding-001": 768,
        "gemini-embedding-001": 768,
    }

    def __init__(
        self,
        model: str = "text-embedding-004",
        api_key: str | None = None,
    ):
        """
        Args:
            model: Gemini embedding model name
            api_key: API key (defaults to environment variable)
        """
        try:
            from google import genai
        except ImportError:
            raise RuntimeError(
                "GeminiEmbedding requires 'google-genai' library. "
                "Install with: pip install google-genai"
            )

        self.model_name = model
        # Use lookup table if available, otherwise detect lazily from first embedding
        self._dimension = self.MODEL_DIMENSIONS.get(model)

        # Resolve API key
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise ValueError(
                "Gemini API key required. Set GEMINI_API_KEY or GOOGLE_API_KEY"
            )

        self._client = genai.Client(api_key=key)

    @property
    def dimension(self) -> int:
        """Get embedding dimension for the model (detected lazily if unknown)."""
        if self._dimension is None:
            # Unknown model: detect from first embedding
            test_embedding = self.embed("dimension test")
            self._dimension = len(test_embedding)
        return self._dimension

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        result = self._client.models.embed_content(
            model=self.model_name,
            contents=text,
        )
        embedding = list(result.embeddings[0].values)
        # Cache dimension if not yet known
        if self._dimension is None:
            self._dimension = len(embedding)
        return embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        result = self._client.models.embed_content(
            model=self.model_name,
            contents=texts,
        )
        return [list(e.values) for e in result.embeddings]


class OllamaEmbedding:
    """
    Embedding provider using Ollama's local API.

    Requires: Ollama running locally.
    Respects OLLAMA_HOST env var (default: http://localhost:11434).
    """

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str | None = None,
    ):
        """
        Args:
            model: Ollama model name
            base_url: Ollama API base URL (default: OLLAMA_HOST or http://localhost:11434)
        """
        self.model_name = model
        if base_url is None:
            base_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        if not base_url.startswith("http"):
            base_url = f"http://{base_url}"
        self.base_url = base_url.rstrip("/")
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        """Get embedding dimension (determined on first embed call)."""
        if self._dimension is None:
            # Generate a test embedding to determine dimension
            test_embedding = self.embed("test")
            self._dimension = len(test_embedding)
        return self._dimension

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        import requests

        response = requests.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.model_name, "prompt": text},
        )
        response.raise_for_status()

        embedding = response.json()["embedding"]

        if self._dimension is None:
            self._dimension = len(embedding)

        return embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts (sequential for Ollama)."""
        return [self.embed(text) for text in texts]


class VoyageEmbedding:
    """
    Embedding provider using Voyage AI's REST API.

    Voyage AI is Anthropic's recommended embedding partner.
    Works well in Claude Desktop and other Anthropic-integrated environments.

    Uses direct HTTP calls - no voyageai SDK needed (avoids heavy dependencies).
    Includes automatic retry with exponential backoff for rate limits.

    Requires: VOYAGE_API_KEY environment variable.
    """

    # Model dimensions (as of 2025)
    # All current models default to 1024 dims
    MODEL_DIMENSIONS = {
        "voyage-3-large": 1024,
        "voyage-3.5": 1024,
        "voyage-3.5-lite": 1024,
        "voyage-code-3": 1024,
    }

    API_URL = "https://api.voyageai.com/v1/embeddings"

    # Retry settings
    MAX_RETRIES = 5
    INITIAL_BACKOFF = 1.0  # seconds
    MAX_BACKOFF = 60.0  # seconds

    def __init__(
        self,
        model: str = "voyage-3.5-lite",
        api_key: str | None = None,
    ):
        """
        Args:
            model: Voyage embedding model name
            api_key: API key (defaults to environment variable)
        """
        self.model_name = model
        # Use lookup table if available, otherwise detect lazily from first embedding
        self._dimension = self.MODEL_DIMENSIONS.get(model)

        # Resolve API key
        self._api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Voyage API key required. Set VOYAGE_API_KEY environment variable.\n"
                "Get your API key at: https://dash.voyageai.com/"
            )

    @property
    def dimension(self) -> int:
        """Get embedding dimension for the model (detected lazily if unknown)."""
        if self._dimension is None:
            # Unknown model: detect from first embedding
            test_embedding = self.embed("dimension test")
            self._dimension = len(test_embedding)
        return self._dimension

    def _request_with_retry(self, payload: dict, timeout: int) -> dict:
        """Make API request with exponential backoff retry for rate limits."""
        import time
        import requests

        backoff = self.INITIAL_BACKOFF
        last_exception = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = requests.post(
                    self.API_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout,
                )

                # Handle rate limiting (429)
                if response.status_code == 429:
                    # Check for Retry-After header
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait_time = float(retry_after)
                        except ValueError:
                            wait_time = backoff
                    else:
                        wait_time = backoff

                    wait_time = min(wait_time, self.MAX_BACKOFF)
                    time.sleep(wait_time)
                    backoff = min(backoff * 2, self.MAX_BACKOFF)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.exceptions.RequestException as e:
                last_exception = e
                error_msg = str(e).lower()

                # Network errors - provide helpful message
                if "connection" in error_msg or "network" in error_msg or "resolve" in error_msg:
                    raise RuntimeError(
                        f"Cannot reach Voyage AI API: {e}\n\n"
                        "If running in a sandboxed environment (e.g., Claude Desktop):\n"
                        "Add api.voyageai.com to your network allowlist."
                    ) from e

                # Other request errors - retry with backoff
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, self.MAX_BACKOFF)
                    continue
                raise

        # Exhausted retries
        raise RuntimeError(
            f"Voyage AI API rate limit exceeded after {self.MAX_RETRIES} retries. "
            "Please wait and try again."
        ) from last_exception

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        data = self._request_with_retry(
            payload={
                "input": [text],
                "model": self.model_name,
                "input_type": "document",
            },
            timeout=60,
        )

        embedding = data["data"][0]["embedding"]

        # Cache dimension if not yet known
        if self._dimension is None:
            self._dimension = len(embedding)
        return embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        if not texts:
            return []

        data = self._request_with_retry(
            payload={
                "input": texts,
                "model": self.model_name,
                "input_type": "document",
            },
            timeout=120,
        )

        # Sort by index to ensure order matches input
        sorted_data = sorted(data["data"], key=lambda x: x["index"])
        return [d["embedding"] for d in sorted_data]


# Register providers
_registry = get_registry()
_registry.register_embedding("sentence-transformers", SentenceTransformerEmbedding)
_registry.register_embedding("openai", OpenAIEmbedding)
_registry.register_embedding("gemini", GeminiEmbedding)
_registry.register_embedding("ollama", OllamaEmbedding)
_registry.register_embedding("voyage", VoyageEmbedding)
