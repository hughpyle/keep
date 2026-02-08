---
tags:
  category: system
  context: tag-description
---
# Tag: `act` — Speech-Act Category

The `act` tag classifies an item by what kind of speech act it performs, grounded in Searle's taxonomy and the Language-Action Perspective (Winograd & Flores).

Tagging items by speech act makes the structure of work visible: what has been promised, what has been requested, what is being offered, what has been asserted.

## Values

| Value | Searle Category | What it marks | Example |
|-------|----------------|---------------|---------|
| `commitment` | Commissive | A promise or pledge to act | "I'll fix auth by Friday" |
| `request` | Directive | Asking someone to do something | "Please review the PR" |
| `offer` | Pre-commissive | Proposing to do something (not yet committed) | "I could refactor the cache layer" |
| `assertion` | Assertive | A claim of fact | "The tests pass on main" |
| `assessment` | Evaluative | A judgment or evaluation | "This approach is risky" |
| `declaration` | Declarative | Changing reality by utterance | "Released v0.23.0" |

## Lifecycle pairing

Three act values represent open-ended speech acts that have a lifecycle: `commitment`, `request`, and `offer`. These pair naturally with the `status` tag to track state (`open`, `fulfilled`, `declined`, `withdrawn`, `renegotiated`). See `keep get .tag/status` for details.

The other three — `assertion`, `assessment`, `declaration` — are typically complete at the moment of utterance and don't need lifecycle tracking.

## Relationship to `type`

The `act` tag is orthogonal to `type`. An item can be both `type=learning` and `act=assessment` — the learning is *about* an assessment. Or `type=breakdown` and `act=commitment` — the breakdown occurred in a commitment.

## Examples

```bash
# Track a commitment with lifecycle
keep put "I'll review the PR tomorrow" -t act=commitment -t status=open -t topic=code-review

# Record a request
keep put "Please add error handling to the API endpoints" -t act=request -t status=open -t project=myapp

# Capture an offer
keep put "I could refactor the cache layer if that would help" -t act=offer -t status=open

# State a fact
keep put "The CI pipeline passes on main as of today" -t act=assertion

# Make an evaluation
keep put "The current auth approach won't scale past 10k users" -t act=assessment -t topic=auth

# Declare a change
keep put "Released v2.0 — new API is live" -t act=declaration -t project=myapp

# Query open commitments
keep list -t act=commitment -t status=open

# Query all requests, any status
keep list -t act=request

# Find commitments in a project
keep find "auth" -t act=commitment -t project=myapp
```
