/**
 * Manual quoting for the room composer.
 *
 * Members keep separate sessions, so "what does the agent know" is a choice the
 * user makes. Automatic context (the last N room entries) is a fallback; these
 * helpers build the explicit alternative: the entries the user ticked, verbatim,
 * with the ask separated from the quotes so the agent cannot confuse the two.
 */
import type { RoomMessageView } from "./rooms-state.ts";

export interface QuoteEntry {
  label: string;
  text: string;
}

const QUOTE_HEADER = "[引用]";
const ASK_HEADER = "[本次请求]";

/** Per-entry clip: a quote is context, not an archive. */
export const QUOTE_ENTRY_LIMIT = 1200;

export function toQuoteEntry(message: RoomMessageView, limit = QUOTE_ENTRY_LIMIT): QuoteEntry {
  const body = message.text.trim();
  return {
    label: message.author_label,
    text: body.length > limit ? `${body.slice(0, limit)}…` : body,
  };
}

/**
 * The message text sent when quotes are attached.
 *
 * With no entries this is just the ask, so callers can use it unconditionally.
 */
export function quoteText(entries: QuoteEntry[], ask: string): string {
  const trimmed = ask.trim();
  if (entries.length === 0) return trimmed;
  const lines = [QUOTE_HEADER];
  for (const entry of entries) {
    lines.push(`【${entry.label}】`);
    lines.push(entry.text);
  }
  lines.push(ASK_HEADER);
  lines.push(trimmed);
  return lines.join("\n");
}

/** Formatting-free summary for the composer chip, e.g. "引用 3 条". */
export function quoteSummary(count: number): string {
  return count > 0 ? `引用 ${count} 条` : "";
}
