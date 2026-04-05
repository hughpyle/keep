# OpenRouter provider

## Shape

Add a first-class `openrouter` provider for embeddings and summarization.

Do not overload the existing `openai` provider. keep already treats
OpenAI-compatible local servers as `openai` (via `base_url`); OpenRouter is
different enough — prefixed model names, optional upstream routing, extra
headers, separate API key — that a dedicated provider keeps config and error
messages clean.

## Config

```toml
[embedding]
name = "openrouter"
model = "openai/text-embedding-3-small"

[summarization]
name = "openrouter"
model = "openai/gpt-4o-mini"
```

Optional fields (both sections):

| Field      | Default                              | Notes                                  |
|------------|--------------------------------------|----------------------------------------|
| api_key    | `$OPENROUTER_API_KEY`                | Explicit override                      |
| base_url   | `https://openrouter.ai/api/v1`      | Unlikely to change; present for parity |
| site_url   | —                                    | Maps to `HTTP-Referer` header          |
| app_name   | —                                    | Maps to `X-OpenRouter-Title` header    |

Embedding-only:

| Field      | Default | Notes                                                      |
|------------|---------|------------------------------------------------------------|
| dimensions | —       | Passed through to API; overrides native model dimension    |

Model names are always in OpenRouter's prefixed form (`openai/text-embedding-3-small`,
not `text-embedding-3-small`). The provider never rewrites or strips prefixes.

## Implementation

### Client factory

Extend `create_openai_client()` with one new parameter:

```python
def create_openai_client(
    api_key: str | None = None,
    base_url: str | None = None,
    default_headers: dict[str, str] | None = None,  # new
):
```

Auth still flows through the SDK's `api_key=` parameter (standard Bearer
token). `default_headers` is only for the OpenRouter-specific headers
(`HTTP-Referer`, `X-OpenRouter-Title`). This keeps the common case (plain
OpenAI, local servers) untouched.

### Provider classes

Two classes in a new `keep/providers/openrouter.py`:

**`OpenRouterEmbedding`**
- `__init__(model, api_key, base_url, dimensions, site_url, app_name)`
- Resolves key from `api_key` param → `OPENROUTER_API_KEY` env var
- `base_url` defaults to `https://openrouter.ai/api/v1`
- `model_name` = the full prefixed model string (used as-is for cache keys
  and `EmbeddingIdentity`; the existing `.key` property handles slashes via
  `replace("/", "_")`)
- `MODEL_DIMENSIONS` keyed by prefixed names:
  `{"openai/text-embedding-3-small": 1536, "openai/text-embedding-3-large": 3072}`
- Falls back to lazy dimension detection (probe embedding) for unknown models
- If `dimensions` is set, passes it through to the API call and uses it as
  the known dimension
- Maps `EmbedTask.DOCUMENT` → `input_type="search_document"`,
  `EmbedTask.QUERY` → `input_type="search_query"` — passed via the SDK's
  `extra_body` parameter

**`OpenRouterSummarization`**
- `__init__(model, api_key, base_url, max_tokens, site_url, app_name)`
- Same key/url resolution as embedding
- Implements both `summarize()` and `generate()` (required for analyzer use)
- Uses standard OpenAI chat completion via the SDK; no special request-body
  modifications needed

Both register at module load:

```python
_registry = get_registry()
_registry.register_embedding("openrouter", OpenRouterEmbedding)
_registry.register_summarization("openrouter", OpenRouterSummarization)
```

### Wizard

Add OpenRouter to `keep/setup_wizard.py`:

- Only shown when `OPENROUTER_API_KEY` is set
- Default models:
  - Embedding: `openai/text-embedding-3-small`
  - Summarization: `openai/gpt-4o-mini`
- Detection priority (unchanged principle):
  1. Direct provider keys (Voyage, OpenAI, Gemini, Mistral, Anthropic)
  2. OpenRouter
  3. Local (Ollama, MLX, sentence-transformers)

### Error handling

No special translation needed. The OpenAI SDK raises standard `APIError`
variants for HTTP errors; OpenRouter's error messages flow through naturally.
Credit exhaustion and model-unavailable errors surface as `APIError` with
descriptive messages from OpenRouter.

### Interactions

- `KEEP_LOCAL_ONLY=1` correctly excludes OpenRouter (no local `base_url`).
- Embedding cache: prefixed `model_name` means OpenRouter and direct OpenAI
  embeddings never collide.
- Retry: the OpenAI SDK's built-in retry with exponential backoff handles
  transient errors. No custom retry logic needed.

### Fix: embedding identity compatibility (not OpenRouter-specific)

`_validate_embedding_identity()` currently compares `(provider, model, dimension)`.
This triggers a full reindex when switching between providers that produce
identical vectors — e.g., `openai` + `text-embedding-3-small` vs `openrouter`
+ `openai/text-embedding-3-small`. Same underlying model, same vectors, wasted
reindex.

The fix: compare on **canonical model + dimension**, not provider name.

**`EmbeddingIdentity` changes:**

Add a `canonical_model` property that strips vendor prefixes used by routing
services:

```python
@property
def canonical_model(self) -> str:
    """Model name without vendor routing prefix.

    OpenRouter uses 'openai/text-embedding-3-small';
    direct OpenAI uses 'text-embedding-3-small'. Same model.
    """
    # Strip leading vendor prefix (e.g., "openai/", "cohere/")
    if "/" in self.model:
        return self.model.split("/", 1)[1]
    return self.model
```

**`_validate_embedding_identity()` changes:**

Replace the three-field comparison with a two-field comparison:

```python
if (stored.canonical_model != current.canonical_model or
    stored.dimension != current.dimension):
```

The identity still *records* the full provider and model (for diagnostics and
`key` generation), but compatibility is determined by canonical model +
dimension only.

**`provider_short` dict:** add `"openrouter": "openrouter"` to avoid the
fallback to `self.provider[:6]` → `"openro"`.

**`key` property:** use `canonical_model` for the slug so that OpenRouter and
direct OpenAI resolve to the same collection name. This means switching
providers reuses the existing search index — which is correct, since the
vectors are identical.

**Testing:**
- Unit: `EmbeddingIdentity("openai", "text-embedding-3-small", 1536)` and
  `EmbeddingIdentity("openrouter", "openai/text-embedding-3-small", 1536)`
  must have equal `canonical_model` and equal `key`.
- Integration: switching between these two identities must NOT trigger reindex.
- Integration: switching to a genuinely different model (e.g., Voyage) MUST
  trigger reindex.

## What not to do

- Don't use `openrouter/auto` for summarization defaults — unpredictable model selection.
- Don't silently rewrite bare model names into prefixed form.
- Don't mix OpenRouter env/config into the existing `openai` provider.
- Don't put Authorization in `default_headers` — use the SDK's `api_key=` parameter.

## Recommended starter models

- Embedding: `openai/text-embedding-3-small`
- Summarization: `openai/gpt-4o-mini`

## Resolved decisions

Verified against the hermes-agent OpenRouter client
(`hermes-agent/agent/auxiliary_client.py`), which is a production-grade
OpenRouter harness.

### 1. Provider routing — skip

OpenRouter supports upstream provider routing via a request-body `provider`
field, but hermes-agent doesn't use it. The prefixed model names
(`openai/...`, `google/...`) already imply routing. No config field needed;
add later if users ask for it.

### 2. Always use `max_tokens`

OpenRouter normalizes the token parameter. The hermes-agent only uses
`max_completion_tokens` for direct `api.openai.com` calls; all OpenRouter
calls use `max_tokens`. So `OpenRouterSummarization` does not need the
new-API detection logic from `OpenAISummarization` — always `max_tokens`.

### 3. Always pass `input_type`

The hermes-agent freely passes extra fields via `extra_body`. OpenRouter and
upstream providers ignore unknown fields. Always map `EmbedTask` →
`input_type` without gating on model name.

## Reference

The hermes-agent OpenRouter client (`/Users/hugh/play/hermes-agent/`) is the
reference implementation for OpenRouter integration patterns:

- `agent/auxiliary_client.py` — provider resolution, header construction,
  `max_tokens` handling, `extra_body` patterns
- `tools/openrouter_client.py` — thin wrapper with lazy init
- Headers: `HTTP-Referer`, `X-OpenRouter-Title`, `X-OpenRouter-Categories`
- Auth: always via SDK `api_key=`, never in `default_headers`
