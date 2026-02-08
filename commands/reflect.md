---
description: Reflect on current actions, commitments, and intentions
---

# Reflect

What is a mirror for? For reflection.

Actions — what you have done, what you are doing, what you intend to do — are to be reflected upon. This command is the practice.

## 1. Gather

See what is, before analyzing.

```bash
keep now
keep list --since P1D # or search, filter by tag, get context
```

Read the current intentions. Review what has been done since the last reflection. Note what you see.

**Index important documents.** Before analyzing, capture any documents (URLs, files, references) encountered since the last reflection that are important to the user or task:
```bash
keep put "https://docs.example.com/api" -t topic=integration -t project=current
keep put "file:///path/to/design.pdf" -t type=reference
```

What is this document? Why is it important to this work? Tag appropriately. This creates navigable knowledge for future sessions.

## 2. The Mirror

For each significant action taken since the last reflection, ask:

*Did this action lead to self-harm, to the harming others, or to both? Was it unskillful — with painful consequences, painful results?*

- If unskillful: name it honestly. What assumption was wrong? What should be done differently? Capture the breakdown:
  ```bash
  keep put "Assumed X, actually Y. Next time: Z" -t type=breakdown
  ```

- If skillful: stay refreshed and joyful. What made it work? Continue training.

Do not skip this step. Do not produce the appearance of reflection without actually reflecting.

## 3. The Conversation

Work is commitment management. Recognize the structure of what is happening.

**What kind of conversation is active?**
- **Action**: Commitments are being made. Request → Promise → Perform → Declare Complete → Declare Satisfied. *Satisfaction* — not completion — closes the loop. Who declares satisfaction here?
- **Possibility**: Options are being explored. Hold lightly. Nothing is promised. Should fail if no actionable commitments emerge.
- **Clarification**: Interpretations are being resolved. What might be misunderstood about conditions of satisfaction?
- **Orientation**: Shared background is being established for future work.

**Where are we in the commitment loop?**
- What has been requested? What has been promised?
- What conditions of satisfaction were established?
- Are there open commitments — unfulfilled promises, unacknowledged completions?

**Standing commitments.** Search keep for ongoing promises and recurring patterns:
```bash
keep find "commitment" --since P30D
keep find "pattern" -t type=pattern
keep find "always" -t act=commitment
keep list -t act=commitment -t status=open
keep list -t act=request -t status=open
```
These are the "as always, please..." requests — promises that persist across sessions. Are they being honored?

**Moods.** A person's mood is driven by their vision of the future. What mood is present in this work? Ambition, serenity, acceptance, and respect serve. Anxiety, resentment, and resignation undermine. To shift a mood, create a different understanding about the future.

**Assessments vs. assertions.** Am I stating facts (assertions) or making evaluations (assessments)? Are my assessments grounded — based on observable evidence? Ungrounded assessments distort action.

**Breakdowns.** Where has the normal flow been interrupted? Breakdowns are valuable — they reveal assumptions that were invisible. Name them.

**Trust.** Both unfulfilled promises and unnecessary requests destroy trust. Are there either?

## 4. Ownership

*I am the owner of my deeds and heir to my deeds. Deeds are my womb, my relative, and my refuge. I shall be the heir of whatever deeds I do, whether good or bad.*

- What patterns are forming through these actions?
- What should I exercise restraint about going forward?

## 5. Update

Based on the reflection, update what needs updating.

If intentions have changed:
```bash
keep now "Updated intentions based on reflection" # -t to apply tags appropriate to the situation
```

If there are learnings to capture:
```bash
keep put "What I learned" -t type=learning
```

If conversation patterns were recognized:
```bash
keep put "Pattern observed" -t type=conversation_pattern
```

If commitments need tracking:
```bash
keep put "I'll address the performance issue next session" -t act=commitment -t status=open
```

Present a brief summary of the reflection to the user. The value is in the reflection itself, not in lengthy output.
