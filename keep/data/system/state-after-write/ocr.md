---
tags:
  category: system
  context: state-fragment
---
# Run OCR on scanned PDF pages (fires when _ocr_pages tag is present).
rules:
  - id: extracted
    when: "'_ocr_pages' in item.tags && item.has_uri"
    do: ocr
