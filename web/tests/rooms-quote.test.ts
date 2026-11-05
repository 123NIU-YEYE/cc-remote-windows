import assert from "node:assert/strict";

import { quoteSummary, quoteText, toQuoteEntry, QUOTE_ENTRY_LIMIT } from "../src/rooms-quote.ts";
import type { RoomMessageView } from "../src/rooms-state.ts";

function message(text: string, label = "Claude · iris", kind: "user" | "member" = "member"): RoomMessageView {
  return {
    msg_id: `m-${text.slice(0, 4)}`,
    ts: 1,
    author_kind: kind,
    author_label: label,
    text,
    mentions: [],
  };
}

// No quotes: the ask goes through untouched.
assert.equal(quoteText([], "  直接发问  "), "直接发问");
assert.equal(quoteText([], ""), "");

// One quote keeps author and body together, with the ask clearly last.
const one = quoteText([toQuoteEntry(message("建议先加集成测试"))], "按这个执行");
assert.ok(one.startsWith("[引用]\n【Claude · iris】\n建议先加集成测试"));
assert.ok(one.includes("[本次请求]\n按这个执行"), one);

// Several quotes preserve the order the user ticked them in.
const many = quoteText([
  toQuoteEntry(message("第一条", "Claude · iris")),
  toQuoteEntry(message("第二条", "Codex · nyx")),
  toQuoteEntry(message("用户的提问", "你", "user")),
], "综合上面三条给出结论");
assert.ok(many.indexOf("第一条") < many.indexOf("第二条"));
assert.ok(many.indexOf("第二条") < many.indexOf("用户的提问"));
assert.ok(many.endsWith("综合上面三条给出结论"));
assert.equal(many.split("[本次请求]").length, 2, "the ask header appears once");

// A long entry is clipped so one message cannot dominate the context.
const long = toQuoteEntry(message("x".repeat(QUOTE_ENTRY_LIMIT + 500)));
assert.equal(long.text.length, QUOTE_ENTRY_LIMIT + 1);
assert.ok(long.text.endsWith("…"));

// Whitespace-only bodies do not produce stray blank blocks.
const blank = toQuoteEntry(message("   "));
assert.equal(blank.text, "");

assert.equal(quoteSummary(0), "");
assert.equal(quoteSummary(3), "引用 3 条");

console.log("rooms-quote.test.ts OK");
