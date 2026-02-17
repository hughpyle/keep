# Plan: OCR Support for Scanned PDFs and Image Documents

## Problem

Scanned PDFs (no text layer) fail hard: `_extract_pdf_text()` raises
`IOError("No text extracted")`. Image documents (PNG/JPG of text) get a
visual description via `MLXVisionDescriber` but no structured text extraction.
There's no OCR path at all.

## Model Choice

**GLM-OCR** (`mlx-community/GLM-OCR-bf16`, also quantized variants)
- 0.9B params, ~2-3GB memory, 1-3s/page on Apple Silicon
- #1 on OmniDocBench V1.5 (94.6%) — beats models 10-70x its size
- Text, tables, formulas, handwriting, multilingual
- Runs via `mlx-vlm` (same library keep already uses for MLXVisionDescriber)

Fallback options if GLM-OCR doesn't work well in practice:
- LightOnOCR-2 (1B, 83.2 OlmOCR-Bench, fast multilingual)
- Granite-Docling (258M, structured output, tiny)

## PDF Page Rendering

**pypdfium2** (`pip install pypdfium2`)
- Apache 2.0 licensed — clean for MIT project
- Pip-installable, bundles PDFium (Chrome's PDF engine), ~10MB wheel
- No system deps (unlike pdf2image/poppler)
- Cross-platform (macOS, Linux, Windows)
- Simple API: `page.render(scale=2)` → bitmap → PIL image

```python
import pypdfium2 as pdfium
pdf = pdfium.PdfDocument(path)
for i in range(len(pdf)):
    page = pdf[i]
    bitmap = page.render(scale=2)  # 2x for OCR quality
    pil_image = bitmap.to_pil()
    # save to temp file, pass to OCR
```

Alternatives considered and rejected:
- **pdf2image** (poppler): requires system-level C library, breaks pure-pip install
- **pymupdf**: AGPL licensed, incompatible with MIT
- **Apple PDFKit** (pyobjc): macOS-only, awkward CGContext API, no cloud path
- **pypdf image extraction**: can't render pages, only extracts embedded images

## Architecture

### New class: `MLXOCRProvider` in `keep/providers/mlx.py`

```python
class MLXOCRProvider:
    """OCR using GLM-OCR via mlx-vlm on Apple Silicon."""

    OCR_PROMPT = "Extract all text from this document image exactly as written."

    def __init__(self, model="mlx-community/GLM-OCR-bf16", max_tokens=2000):
        from mlx_vlm import load as vlm_load
        self.model_name = model
        self.max_tokens = max_tokens
        self._model, self._processor = vlm_load(model)

    def extract_text(self, image_path: str) -> str | None:
        """OCR a single image, return extracted text or None."""
        from mlx_vlm import generate as vlm_generate
        response = vlm_generate(
            self._model, self._processor,
            prompt=self.OCR_PROMPT,
            image=image_path,
            max_tokens=self.max_tokens,
            verbose=False,
        )
        return response.strip() if response else None
```

Follows the exact same pattern as `MLXVisionDescriber` (lines 285-334).
Separate class because the prompt, model, and max_tokens are different —
OCR needs much more output tokens than image description.

### Integration Point A: Per-page OCR fallback in PDF extraction

**File:** `keep/providers/documents.py`, `_extract_pdf_text()` (line 413)

Key insight: OCR should happen **per-page**, not per-document. Real PDFs
are often mixed — typed pages alongside scanned signature pages, appendices,
etc. Per-page fallback handles this correctly:

```python
def _extract_pdf_text(self, path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(path)
    text_parts = []
    ocr_needed = []

    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            text_parts.append((i, text))
        else:
            ocr_needed.append(i)

    if ocr_needed:
        ocr_texts = self._ocr_pages(path, ocr_needed)
        for i, text in ocr_texts:
            text_parts.append((i, text))

    if not text_parts:
        raise IOError(f"No text extracted from PDF: {path}")

    # Sort by page number, join
    text_parts.sort(key=lambda t: t[0])
    return "\n\n".join(text for _, text in text_parts)
```

When to OCR:
- **Page has text** → use pypdf text (fast, reliable)
- **Page has no text** → render to image via pypdfium2, OCR via GLM-OCR
- **OCR not available** (no mlx-vlm/pypdfium2) → skip those pages, or
  raise IOError if zero text from any page

This avoids double-extracting text pages and correctly handles mixed docs.

Edge case: PDFs where the text layer exists but is garbage (bad encoding).
pypdf returns "text" so we don't OCR. Not worth solving in v1 — detecting
mojibake reliably is a rabbit hole.

### Integration Point B: Always-both for images (description + OCR)

Every image gets visual description (existing). Additionally, OCR runs
on every image and includes extracted text when significant (>20 chars).
No config needed — the right thing happens automatically:

- **Sunset photo:** description only (OCR finds nothing, section omitted)
- **Whiteboard photo:** description + OCR text (both useful for search)
- **Photo of document:** description + OCR text (OCR is the primary value)

Output format appended to document content:

```
Description:
A whiteboard covered with handwritten architecture diagrams...

OCR Text:
Auth Service → API Gateway → Database
Rate limit: 100 req/min
```

Implementation in `MLXMediaDescriber._describe_image()`:

```python
def _describe_image(self, path, content_type):
    parts = []
    # Visual description (existing)
    desc = self._vision.describe(path, content_type) if self._vision else None
    if desc:
        parts.append(desc)
    # OCR enrichment (new — lazy, only if mlx-vlm available)
    ocr_text = self._ocr.extract_text(path) if self._ocr else None
    if ocr_text and len(ocr_text) > 20:  # skip trivial text
        parts.append(f"OCR Text:\n{ocr_text}")
    return "\n\n".join(parts) if parts else None
```

The OCR provider loads lazily on first image — silently skipped if deps
missing. Both outputs improve searchability: description gives semantic
meaning, OCR gives exact text.

### Background queue integration

OCR at 1-3s/page is too slow for synchronous `put()` on multi-page docs.

For PDFs with >2 pages needing OCR, the pattern is:
1. `put()` extracts what text it can from text-layer pages synchronously
2. For scanned pages, store placeholder: `"[page N: OCR pending]"`
3. Enqueue an OCR task: `self._pending_queue.enqueue(id, coll, path, task_type="ocr", metadata={"pages": ocr_needed})`
4. `process_pending()` handles `"ocr"` task type:
   - Render the specified pages, run OCR on each
   - Replace placeholder markers with actual OCR text
   - Re-embed with the full content

This follows the existing pattern for `"summarize"` and `"analyze"` tasks.

For ≤2 scanned pages, run OCR synchronously (2-6s is acceptable).

### Cloud/hosted mode

Scoped to local/MLX only for now. Cloud options for later:
- Cloud VLM API (Gemini, OpenAI GPT-4o) as OCR — works, expensive per page
- Dedicated OCR service (Google Document AI, AWS Textract) — cheapest
- Run GLM-OCR on a GPU sidecar — reuses the same model

## File Changes

| File | Change |
|------|--------|
| `keep/providers/mlx.py` | Add `MLXOCRProvider` class (~40 lines) |
| `keep/providers/mlx.py` | Add OCR to `MLXMediaDescriber` for image-of-text |
| `keep/providers/documents.py` | Per-page OCR fallback in `_extract_pdf_text()` |
| `keep/api.py` | Add `"ocr"` task type in `process_pending()` |
| `keep/api.py` | Enqueue OCR for multi-page scanned PDFs |
| `keep/config.py` | Add `ocr` provider config, auto-detect on Apple Silicon |
| `pyproject.toml` | Optional dep: `pypdfium2` in `[local]` extra |
| `tests/` | Test OCR fallback with a scanned PDF fixture |

## Dependencies

- `mlx-vlm` (already optional dep via `[local]`)
- `pypdfium2` (new optional dep — Apache 2.0, pip-installable, ~10MB, no system deps)

```toml
[project.optional-dependencies]
local = [
    "mlx>=0.10; ...",
    "mlx-lm>=0.10; ...",
    "sentence-transformers>=2.2",
    "pypdfium2>=4.0",   # PDF page rendering for OCR
]
```

## Incremental delivery

1. **Phase 1:** `MLXOCRProvider` + per-page PDF fallback (sync, ≤2 pages)
2. **Phase 2:** Background queue for multi-page OCR
3. **Phase 3:** Image-of-text OCR enrichment in MediaDescriber
4. **Phase 4:** Cloud provider (Gemini/GPT-4o OCR endpoint)

## Design decisions

- **Per-page fallback, not per-document.** Text pages use pypdf, scanned
  pages get OCR'd. Handles mixed documents correctly. Never double-extracts.
- **Automatic when text extraction fails.** No opt-in config flag needed.
  If mlx-vlm and pypdfium2 are installed, OCR happens transparently.
  Config override to disable: `[ocr] enabled = false`.
- **Separate model from vision describer.** GLM-OCR (0.9B) is purpose-built
  for text extraction. The vision describer (Qwen2-VL-2B) is for image
  understanding. Different prompts, different output lengths, different
  strengths. Loading both costs ~5GB, fine on 16GB machines.
- **pypdfium2 over poppler/pymupdf.** Apache 2.0, pip-installable, no
  system deps, cross-platform. The only clean option.
