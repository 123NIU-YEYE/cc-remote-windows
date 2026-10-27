import assert from "node:assert/strict";

import {
  handleFor, initialOf, labelSegments, mentionAt, mentionCandidates,
} from "../src/rooms-mentions.ts";
import type { RoomMemberView } from "../src/rooms-state.ts";

const members: RoomMemberView[] = [
  { member_id: "m1", label: "Claude · iris", engine: "claude" },
  { member_id: "m2", label: "Codex · nyx", engine: "codex" },
];

assert.deepEqual(labelSegments("Claude · iris"), ["Claude", "iris"]);

// Distinct engines: the engine name is the shortest unique handle.
assert.equal(handleFor(members[0], members), "Claude");
assert.equal(handleFor(members[1], members), "Codex");

// Two members of the same engine: fall back to the agent name.
const twins: RoomMemberView[] = [
  { member_id: "a", label: "Claude · iris", engine: "claude" },
  { member_id: "b", label: "Claude · nyx", engine: "claude" },
];
assert.equal(handleFor(twins[0], twins), "iris");
assert.equal(handleFor(twins[1], twins), "nyx");

assert.equal(initialOf(members[0]), "I");

// Candidates always offer the documented broadcast.
assert.deepEqual(mentionCandidates(members, "").map((o) => o.insert),
  ["Claude", "Codex", "全体"], "empty query lists every member then 全体");

assert.deepEqual(mentionCandidates(members, "co").map((o) => o.insert), ["Codex"]);
assert.deepEqual(mentionCandidates(members, "ir").map((o) => o.insert), ["Claude"],
  "the agent name filters to its member");
assert.deepEqual(mentionCandidates(members, "全体").map((o) => o.insert), ["全体"]);
assert.deepEqual(mentionCandidates(members, "zzz"), []);

// Detecting the fragment under the caret.
assert.deepEqual(mentionAt("@", 1), { start: 0, query: "" });
assert.deepEqual(mentionAt("你好 @", 4), { start: 3, query: "" });
assert.deepEqual(mentionAt("@cla", 4), { start: 0, query: "cla" });
assert.deepEqual(mentionAt("hi @Codex 你好", 9), { start: 3, query: "Codex" });
assert.equal(mentionAt("@Claude 已发送", 9), null, "a finished mention is not a fragment");
assert.equal(mentionAt("mail@example.com", 16), null, "mid-word @ is not a mention");
assert.equal(mentionAt("no at sign", 10), null);

console.log("rooms-mentions.test.ts OK");