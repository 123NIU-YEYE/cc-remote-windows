# Claude user questions

Claude's native `AskUserQuestion` reaches cc-remote through the Agent SDK's
`can_use_tool` callback. cc-remote supplies the interface and returns the original
tool input plus `answers`; it does not replace Claude's question generation.
See the [official user-input contract](https://code.claude.com/docs/en/agent-sdk/user-input).

Native questions and the `cc-remote-ask` MCP server share the centered question
form in main and side chats. One call may contain 1–4 questions. Previous/Next
preserve each page's selections, text and scroll position; the last page submits
all answers together. Options indicate selection by color, without radio/check
icons. Multi-select pages explicitly say they accept multiple choices. A single
question uses the same form without pagination. Tool permission approvals keep
their existing single-choice behavior.

Drafts live only in bounded browser memory, scoped to device/account/session and
the pending question identity. Switching sessions or reconnecting in the same
page preserves them. Reloading/closing the browser does not persist draft answers.
The Wrapper re-seeds the complete still-pending batch on reconnect. A browser
keeps the form until the Wrapper confirms its closure; transport failure does
not discard answers. Stop, timeout and another client's accepted answer close
the entire batch. Partial submissions cannot resume Claude.

## MCP input

Existing `ask_user(question, options)` calls still work and return their text
answer. To collect related questions together, pass `questions` instead:

```json
{
  "questions": [
    {
      "header": "Order",
      "question": "How should files be sorted?",
      "options": [
        {"label": "Recent first", "description": "Newest updates first"},
        {"label": "By type", "description": "Group images and documents"}
      ],
      "multiSelect": false
    },
    {
      "header": "Scope",
      "question": "Which files should be included?",
      "options": [{"label": "Uploads"}, {"label": "Generated files"}],
      "multiSelect": true
    }
  ]
}
```

Batch questions use the native field names (`multiSelect` and option
`description`); the legacy single-question API retains option `ds`. Each batch
question has 2–4 unique options and always supports a custom answer. Do not mix
`questions` with top-level `question`/`options`. A batch result is JSON text:

```json
{"answers":[{"question":"How should files be sorted?","answer":"Recent first"},{"question":"Which files should be included?","answer":["Uploads","Generated files"]}]}
```

Question batches were introduced in wire protocol v77 (current protocol v79).
Web, Relay and Wrapper must upgrade together.
It does not change the SDK pin or require restarting an active Claude service.
Existing native processes may keep the old MCP tool schema until their next
ordinary tool discovery/session start; the native question callback is handled
by the newly attached Wrapper. No deployment is performed by editing this code.
