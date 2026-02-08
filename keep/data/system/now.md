---
tags:
  category: system
  context: now
---
# First time

This is `keep` — a reflective memory skill. You're seeing this because the store is new.

**Start here:** Read the full practice guide. It teaches why and how to reflect before, during, and after action.

```bash
cat "$(keep config tool)/SKILL.md"
```

**Then read the foundational teachings** — these are not test data, they are the source material for the practice:

```bash
keep get .conversations
keep get .domains
keep put "file://$(keep config tool)/docs/library/mn61.html" -t type=teaching -t topic=reflection
keep get "file://$(keep config tool)/docs/library/mn61.html"
```

**Now update this nowdoc** with your understanding of the practice and your current intentions:

```bash
keep now "My understanding of the practice: ..."
```

Once you do, this first-time message is replaced by your own working context. From then on, `keep now` is your mirror — check it before acting, update it after.
