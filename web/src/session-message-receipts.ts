import type { SessionMessageReceipt } from "./protocol";
import type { Turn } from "./domain/conversation";
import { localSessionReference } from "./session-messages.ts";
const NATIVE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function unifiedReceiptStatus(content: string): SessionMessageReceipt["status"] {
  try {
    const value = JSON.parse(content);
    if (Array.isArray(value) && value.length === 1 && typeof value[0]?.text === "string") {
      return unifiedReceiptStatus(value[0].text);
    }
    return value?.status === "accepted" ? "sent" : value?.status === "rejected" ? "failed"
      : value?.status === "queued" ? "queued" : "sending";
  } catch { return "sending"; }
}

/** Only the native send tool carries an outgoing link, never arbitrary prose. */
export function outgoingSessionMessages(turn: Turn): SessionMessageReceipt[] {
  const receipts = new Map((turn.sessionMessages ?? []).map((r) => [r.itemId, r]));
  for (const block of turn.blocks) {
    if (block.kind !== "tool") continue;
    const unified = ["mcp__cc_remote_messages__send_message", "mcp__cc-remote-ask__send_message"].includes(block.tool)
      || (["send_message", "cc_remote_messages.send_message"].includes(block.tool)
        && (block.server === "cc_remote_messages" || block.input.namespace === "cc_remote_messages"));
    const native = (block.tool === "send_message_to_thread"
        || block.tool === "codex_app.send_message_to_thread")
      && (block.server === "codex_app" || block.input.namespace === "codex_app");
    if (!unified && !native && block.tool !== "mcp__codex_app__send_message_to_thread") continue;
    const target = unified ? block.input.session_ref : block.input.threadId;
    if (typeof target !== "string" || (unified ? !localSessionReference(target) : !NATIVE_ID.test(target))) continue;
    const result = block.result;
    receipts.set(block.tool_use_id, {
      itemId: block.tool_use_id, threadId: target,
      status: !result ? receipts.get(block.tool_use_id)?.status ?? "sending"
        : unified ? unifiedReceiptStatus(result.content)
        : result.is_error || ["failed", "cancelled", "declined", "interrupted"].includes(result.status ?? "")
          ? "failed" : "sent",
    });
  }
  return [...receipts.values()].slice(0, 16);
}
