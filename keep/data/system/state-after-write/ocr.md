---
tags:
  category: system
  context: state-fragment
---
rules:
  - id: extracted
    when: "'_ocr_pages' in item.tags && item.has_uri"
    do: ocr
