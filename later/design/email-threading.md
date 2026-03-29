# Email Threading as Version Strings

## Summary

Email threads are stored as single keep items with one version per message, enabling conversation-style analysis using the existing vstring infrastructure.

## ID Scheme

- **Thread item**: `thread:<root-message-id>` (root = first Message-ID in `References` header, or own Message-ID if no References)
- **Attachment**: `thread:<root-msg-id>#<content-id>` (MIME Content-ID if available, else `att-{N}`)

## How It Works

Each email message is `put` as a **version** of the thread item:

```
Item: thread:<abc123@example.com>
  V{1}  2026-03-14  alice: "Can we meet Thursday?"
  V{2}  2026-03-14  bob: "Thursday 2pm?"
  V{3}  2026-03-15  alice: "Confirmed. See attached."
    └── thread:<abc123@example.com>#<img001@x.com>  (attachment)
  V{4}  2026-03-15  carol: "Adding budget to agenda"
```

- `created_at` set from the email's `Date` header (not insertion time)
- Head item is the latest version inserted (Gmail-style)
- Versions ordered by `created_at` at read time for analysis (not by version number)
- Out-of-order import is fine — chronological ordering happens at analysis time

## Tags

### Per-version tags (stored on each version)
- `from` — sender of that message
- `to`, `cc`, `bcc` — recipients of that message
- `message-id` — this message's unique ID
- `in-reply-to` — parent message-id (reply structure within thread)
- `subject` — may vary across versions ("Re: ..." additions)
- `date` — ISO 8601 from Date header

### Head item tags (accumulated)
- All edge-tags from the latest message (`from`, `to`, etc.)
- `_content_type: message/rfc822`

## Attachments

Each MIME attachment is stored as a separate keep item:
- ID: `thread:<root-msg-id>#<content-id>` or `thread:<root-msg-id>#att-{N}`
- `attachment` edge-tag points to the thread item
- Inherits `from`, `to`, `date`, `subject` from the parent message
- Goes through normal document provider pipeline (OCR, text extraction, etc.)
- Temp files stored in `~/.cache/keep/email-att/`

## Edge Tags

| Tag | `_inverse` | Notes |
|---|---|---|
| `from` | `sender_of` | Already implemented |
| `to` | `recipient_of` | Already implemented |
| `cc` | `cc_recipient_of` | Already implemented |
| `bcc` | `bcc_recipient_of` | Already implemented |
| `attachment` | `has_attachment` | Already implemented |
| `in-reply-to` | `has_reply` | New — links reply to parent within thread |

## Thread ID Resolution

1. Parse `References` header — ordered list of ancestor Message-IDs
2. Thread root = first Message-ID in `References`
3. If no `References`, check `In-Reply-To` — thread root is the In-Reply-To value
4. If neither exists, thread root = own Message-ID (new thread)

## Analysis

The existing vstring analysis (sliding window, incremental decomposition) works directly:
- Each version is a message in the conversation
- Analysis extracts themes, decisions, action items across the thread
- Speaker attribution via `from` tag on each version
- Parts can be tagged with `speaker` for conversation decomposition

## Version Ordering at Read Time

Versions are stored in insertion order (version numbers). For analysis, sort by `created_at` (the message's Date header) to get chronological order. This is a one-line change in the analysis chunk gathering.

## Implementation Notes

- First message in a thread creates the item
- Subsequent messages `put` with the same thread ID, creating new versions
- `_extract_email` returns thread-id and per-message metadata
- `_put_direct` detects `message/rfc822` content type and routes to thread-aware put
- Dedup: if `message-id` already exists as a version tag, skip (idempotent re-import)
