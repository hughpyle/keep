---
tags:
  category: system
  context: prompt
---
# .prompt/agent/session-end

Archive session state at session end.

## Prompt

Archive this session's versions from `now` to keep it clean for the next session.

```
keep move session-{session_id} -t session={session_id}
```
