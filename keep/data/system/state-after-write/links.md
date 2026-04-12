---
tags:
  category: system
  context: state-fragment
---
# Extract links from text-bearing content (markdown, plain text, HTML, email, PDF, DOCX, PPTX).
# Creates stub notes for discovered targets via the stub flow.
rules:
  - id: linked
    when: "!item.is_system_note && item.has_content && (item.content_type == 'text/markdown' || item.content_type == 'text/plain' || item.content_type == 'text/html' || item.content_type == 'message/rfc822' || item.content_type == 'application/pdf' || item.content_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' || item.content_type == 'application/vnd.openxmlformats-officedocument.presentationml.presentation')"
    do: extract_links
    with:
      tag: references
      create_targets: "true"
      doc_links: "{params.metadata.doc_links}"
