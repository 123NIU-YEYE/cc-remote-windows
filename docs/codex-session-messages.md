# Codex cross-session messages

cc-remote supports two message entrances: the native Codex App tools described
below, and the opt-in [unified Claude/Codex MCP](session-messages.md). Both use
the same compact incoming source label and outgoing receipt UI. Installing this
feature does not enable either adapter or grant tool permissions. See
[codex-app-tools.md](codex-app-tools.md) for the shared account/daemon prerequisite.

Incoming messages show **来自会话 · name**. The Wrapper recognizes both the older
`userMessage` envelope and the newer `functionCallOutput` from the `codex_app`
namespace (`create_thread`, `send_message_to_thread`, `handoff_thread`). It keeps
the native item id and source thread id across live delivery, steering, official
history, rollout compatibility reads and browser caches. Unknown/malformed
markup remains ordinary text. Provenance is native display metadata, not an
independent authentication claim.

Native send calls show a compact outgoing receipt with the actual tool outcome.
A failed call does not become “sent”. The small receipt survives summary
materialization when the source includes that tool; for an older summary that
omits tool items, expand the process to read them. Tool bodies stay deferred.

A source/recipient link resolves against the current device's Codex Code catalog
and the current account routing prefix. Missing, deleted, cross-account and
Work targets remain labelled but disabled. The link opens the target in the
normal cc-remote conversation view, using the same history and session-switch
path as a sidebar selection. There is no separate read-only preview. Drafts stay
scoped to their original sessions. Opening a link does not send a message,
interrupt the previous session, force takeover or navigate Codex App; a cold
target may resume through the ordinary session-switch path.

The native envelope supplies a source thread id, not the sending call/turn id.
The link therefore opens that session, without guessing a corresponding record
from matching text. Codex App keeps its own native rendering and source links.
End-to-end sending still requires a running eligible App and its tool adapter.
These restrictions describe the **native** entrance. The unified entrance has
explicit engine/account-qualified, device-local references, so its links can
resolve across configured Code accounts and between Claude and Codex. Neither
form can nominate another device or turn display metadata into authorization.

Native source fields were introduced in protocol **v74** (current: **v78**).
The unified entrance reuses those bounded fields and adds a queued receipt
state; Web, Relay and Wrapper must upgrade together.
Existing history caches remain valid when adding the unified entrance; new
transcript records carry its metadata. Native transcripts, credentials and
original tool outputs are unchanged by history projection.

Validation uses native-shape fixtures without model calls: incoming format and
escaping, exact ids, live/history replay races, same-text separate messages,
steering, sender failure receipts, account/device routing, and desktop/mobile
direct-navigation and draft-preservation checks. Live App/MCP delivery is a
separate acceptance check; fixture success alone does not prove it.
