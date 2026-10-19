# Cross-session messaging MCP

Two entrances share cc-remote's source labels, send receipts and direct
conversation navigation:

- The optional official Codex App `send_message_to_thread` tool keeps its native
  routing and envelopes. See [native messages](codex-session-messages.md).
- `cc_remote_messages` exposes `list_sessions`, `send_message` and
  `message_status` to Codex and Wrapper-managed Claude Code sessions. It calls
  the existing Wrapper query path, which uses official Codex App Server or the
  target Claude SDK connection. It does not create a separate agent/session.

This is a messaging entrance, not a task scheduler. Acceptance is not task
completion. There is no automatic result callback; a target can explicitly use
the same tool to reply to its sender. Only send work requested by the user.

## Enable

Set `CC_REMOTE_SESSION_MESSAGES=1` in the existing Wrapper service environment
and activate through the normal deployment workflow. Default is off. The
Wrapper creates `<CC_REMOTE_STATE_DIR>/messages/control.sock` in a private
directory, with a same-user lifetime lock and mode 0600 socket. It only serves
that Wrapper's local device. Unix socket paths must fit the 103-byte platform
limit; startup fails if the configured state path is too long or unsafe.

New Claude Code SDK connections receive a caller-bound `cc_remote_messages`
server automatically. Existing persistent Claude processes refresh their owned
MCP tool registration before the next input at a safe idle boundary (see below);
enabling this feature does not restart a running Claude service. Work and private BTW sessions do not get
messaging access. This does not inject tools into an independently running
Claude CLI.

For each opted-in Codex account, add to its own `config.toml`:

```toml
[mcp_servers.cc_remote_messages]
command = "/absolute/path/to/cc-remote"
args = ["messages", "mcp", "--socket", "/absolute/state/messages/control.sock",
        "--codex-home", "/absolute/codex-home"]
```

Reload MCP through the official client when safe. Verify the actual thread can
call `list_sessions`, not just that the tool appears in a global catalog. After
upgrades the native client may retain an existing MCP process; explicitly reload
the changed configuration or open a new native session. Do not restart a shared
daemon or a live task to refresh a tool.

The launcher runs `messages` as the invoking user on Linux, without sudo. A
root-owned Wrapper socket cannot be used by a different OS user; do not relax
the socket permissions to work around this. For development, use the repository
venv with `python -m cc_remote.session_messages mcp` and the same two arguments.

No account configuration is edited automatically. No Relay login password,
model API key or provider credential is passed to this MCP.

## Calls and delivery semantics

1. `list_sessions({"search":"review"})` reads recent eligible Code session
   metadata across configured accounts. It returns at most 200 matches with
   `session_ref`, engine, account, title, cwd and resident state. It does not
   start/focus an engine. Archived, Work, background-job and private BTW sessions
   are excluded; unavailable account catalogs are omitted.
2. `send_message({"session_ref":"ccr:codex:primary@<native-id>",
   "message":"Please review this change.","request_id":"review-1"})` submits
   to the selected exact session. Use the reference returned by the catalog.
3. `message_status({"request_id":"review-1"})` reads the sender's retained
   receipt without submitting work.

Codex caller identity comes from native MCP request metadata (`threadId` or
`x-codex-turn-metadata.thread_id`) and the configured account home. Conflicting
or missing metadata fails closed. Claude's source is bound to the current SDK
context, including its latest native ID. The model cannot choose a source via
tool arguments. A source must be an eligible Code session in the local catalog.

Dispatch uses the ordinary ordered mutation lane and the target's query lock.
A cold, known target can resume with its existing cwd/account. There is no
bootstrap-to-new-session fallback or forced takeover. Busy sessions retain the
message in a private durable inbox. Delivery waits for the real terminal boundary,
pending questions/autonomous follow-ups and existing user queues. Externally
owned and unavailable targets are rejected. Main-page focus is unchanged.
Normal native permissions and approval handling remain in force.
Execution uses the **target session's** permission configuration, not the
sending session's temporary sandbox. Do not treat this tool as inheriting a
read-only caller's restrictions when the target has broader permissions.

Receipts have four states:

| State | Meaning |
| --- | --- |
| `queued` | Retained locally, not yet submitted. The Wrapper will deliver after the target is ready, including after a Wrapper restart. |
| `accepted` | The target's native query call accepted the input. Its task may still run or subsequently fail. |
| `rejected` | A known pre-submission check rejected the message; reason is included. |
| `unknown` | Acceptance was not confirmed. The input may have been sent; inspect the target and do not resend under another ID. |

The Wrapper writes an idempotency record **before** submitting a command.
Retrying the same source/request ID with identical arguments returns that
record, including after restart or participant deletion. Reusing it with
different arguments fails. Lost acknowledgments never cause automatic retries.
`message_status` reports send acceptance, not the outcome of the target's work.
There are at most 32 queued/in-flight submissions. Targets have independent FIFO
delivery; one busy session does not block others. The private SQLite store retains
up to 10,000 receipts and rejects further sends at capacity rather than pruning
keys and making an old send repeatable. Only queued entries retain their complete
bounded prompt, in the user's private directory (0700, database 0600). Before
the first possible native write the Wrapper commits an uncertain claim and
erases that body. After restart such a claim is `unknown` and is never replayed.
Only an explicit SDK launch fence proving the input was not submitted may return
it to `queued`. Rejection/acceptance also erases the pending body. Deleting this
state would lose the idempotency guarantees.

## Existing Claude workers

An attached persistent Claude worker retains its startup MCP configuration.
Before its next input, the Wrapper refreshes its owned MCP registration at a
quiescent boundary. A worker predating `cc_remote_messages` exposes the same
caller-bound tools through its existing `cc-remote-ask` proxy instead. The
official SDK `mcp_toggle` off/on path re-lists those tools; a following
`mcp_status` must confirm all three before the input is submitted. A worker
already configured with the dedicated route refreshes that route without
duplicating tools. Pre-existing disabled servers remain disabled.

This does not close the native child, restart the independent service, change
account/cwd, replay an old input, or auto-send a reply. Managed turns, pending
callbacks and tracked background work defer refresh. A message waiting for the
refresh stays queued. Ordinary chat may continue with an unchanged tool set if
background work defers refresh; a partially completed toggle must be repaired
before any new input. Refresh intent remains in the service across controller
loss. Normal question/mode tools remain available afterward.

## Presentation and boundaries

The message text and `ccr:<engine>:<Wrapper-route>` source reference are persisted
as a bounded `cc_remote_message` envelope in the target's native input. Both
live and historical cc-remote projections show the clean message text and
**来自会话 · name**. Independent native clients may display the envelope as text.
The sender's MCP tool result supplies **已排队发送给**, **已发送给**,
**未能发送给**, or an unconfirmed-state label. This is the receipt at tool-return
time; `message_status` reads its latest durable state. The source thread's normal tool history is the receipt
source; no synthetic success message is inserted into its conversation.

Protocol v78 adds the explicit queued receipt state to the source/receipt fields;
Web, Relay and Wrapper must upgrade together. Native
Codex references remain relative to their original account. Explicit unified
references can resolve across configured accounts/engines, but only against
the current device's Code catalogs. Clicking opens the exact target through the
normal conversation-switch path, including its engine and account. There is no
separate preview page. Navigation preserves per-session drafts and does not
send a message, interrupt the previous session or force takeover. A cold target
may resume as it does when selected in the sidebar. Source metadata is for
display and navigation, not authentication.

MCP does not enforce exclusive delivery. A same-user process with sufficient
shell/socket access can use other native entrances. A private local socket
protects against other OS users, not an unrestricted process under the same
user. Hard exclusivity requires separate OS identities/sandboxing and control
of all alternate transports. An unlabelled ordinary native input cannot be
reliably attributed after the fact.

## Validation

`tests/test_session_messages.py` exercises private-socket and real stdio MCP
transport with fake engine dispatch, Claude caller binding, exact target
routing, duplicate/concurrent retries, lost acknowledgments, receipt recovery,
and live/history metadata. Native Codex fixtures remain in
`tests/test_codex_delegation.py`. Web reliability tests cover both source formats
and receipt states. These tests make no live model requests. Deployment and
real Claude/Codex message delivery are separate acceptance checks.
