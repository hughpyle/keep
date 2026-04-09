---
tags:
  category: system
  context: tag-description
---
# Tag: `topic_of` — Inverse Topic Edge

The `topic_of` tag is the inverse of `topic`. It is maintained automatically by the edge processor — you do not set it manually.

When item A has `topic: machine-learning`, the edge processor adds `topic_of: [[A|A's summary]]` to the `.topic/machine-learning` concept node. This makes topic nodes navigable hubs: `keep get .topic/machine-learning` shows everything tagged with that topic.

## Characteristics

- **System-maintained**: Set by the edge processor, not by users or agents directly.
- **Multi-valued**: A topic node accumulates back-edges from every item that references it.
- **Navigable hub**: `keep get .topic/<name>` surfaces all items tagged with that topic.

## Concept nodes

Topic nodes live at `.topic/<name>`. They are auto-vivified when a `topic=<name>` edge is first written. The node's body content can be enriched manually to describe the concept:

```bash
# Enrich a topic node with a description
keep put "Subject area covering statistical learning, neural networks, and model training.
Key subtopics: supervised learning, unsupervised learning, reinforcement learning,
deep learning, embeddings, dimensionality reduction." \
  --id .topic/machine-learning \
  -t topic=machine-learning

# See everything in a topic
keep get .topic/machine-learning
# → topic_of:
# →   - note-a   [2026-04-08] Notes on UMAP...
# →   - note-b   [2026-04-07] Survey of TKG methods...
```

## Injection

This tag is not used in analysis prompts. It is a structural edge maintained by the system.
