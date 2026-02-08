---
tags:
  category: system
  context: tag-description
---
# Tag: `status` — Lifecycle State

The `status` tag tracks the lifecycle state of speech acts — primarily commitments, requests, and offers. It answers: "Where does this stand?"

## Speech-act lifecycle values

| Value | Meaning | Typical transition from |
|-------|---------|------------------------|
| `open` | Active and unfulfilled | (initial state) |
| `blocked` | Cannot proceed until something else is resolved | `open` |
| `fulfilled` | Completed and satisfied | `open` |
| `declined` | Not accepted | `open` |
| `withdrawn` | Cancelled by the originator | `open` |
| `renegotiated` | Terms changed, new commitment replaces this one | `open` |

The normal flow: `open` → `fulfilled`. `blocked` is a sub-state of open — the commitment still exists but progress is gated on an external dependency. The other transitions handle exceptions — declining, withdrawing, or renegotiating.

## Domain-specific status values

The `status` tag is also used in domain contexts with different values. For example, in software development: `working`, `broken`, `needs_review`, `deprecated`. These coexist naturally — the value itself disambiguates. An item with `status=open` is a speech-act lifecycle state; an item with `status=broken` is a domain state.

## Examples

```bash
# Create an open commitment
keep put "I'll fix the auth bug by end of week" -t act=commitment -t status=open -t project=myapp

# Mark it fulfilled
keep tag-update ID --tag status=fulfilled

# Query open items
keep list -t act=commitment -t status=open
keep list -t act=request -t status=open

# Track a request lifecycle
keep put "Please review the PR" -t act=request -t status=open
# Later, when reviewed:
keep tag-update ID --tag status=fulfilled

# Withdraw an offer
keep tag-update ID --tag status=withdrawn

# Renegotiate a commitment (mark old, create new)
keep tag-update ID --tag status=renegotiated
keep put "Revised: will fix auth bug next sprint instead" -t act=commitment -t status=open
```
