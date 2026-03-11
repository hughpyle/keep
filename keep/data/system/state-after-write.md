---
tags:
  category: system
  context: state
---
# Runs after each put(). All matching rules fire in parallel.
# This state doc is the SOLE source of truth for post-write background tasks.
# Do NOT hardcode task enqueue decisions in _put_direct or elsewhere — add
# rules here instead and let _dispatch_after_write_flow() handle dispatch.
match: all
rules:
  - id: summary
    # Long content without an existing summary
    when: "item.content_length > params.max_summary_length && !item.has_summary"
    do: summarize
  - id: extracted
    # URI-backed items with image pages needing OCR
    when: "'_ocr_pages' in item.tags && item.has_uri"
    do: ocr
  - id: described
    # Non-text URI content (images, audio, video) → media description
    when: "item.has_uri && item.has_media_content && system.has_media_provider"
    do: describe
  - id: analyzed
    # Decompose non-system items into parts
    when: "!item.is_system_note"
    do: analyze
  - id: tagged
    # Classify non-system items against tag specs
    when: "!item.is_system_note && item.has_content"
    do: tag
post:
  - return: done
