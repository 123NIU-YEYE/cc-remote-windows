import assert from "node:assert/strict";

import { PROTOCOL_VERSION } from "../src/protocol.ts";
import {
  applyRoomEvent, filterRooms, roomOrder, runningMembers, runningSeconds,
  type RoomsState,
} from "../src/rooms-state.ts";

const base = { v: PROTOCOL_VERSION, ts: 1 };

function roomList(rooms: unknown[], requestId = "r1") {
  return { ...base, type: "room_list" as const, request_id: requestId, rooms } as never;
}

const ROOM = {
  room_id: "room-1",
  name: "cc-remote 协作",
  cwd: "/Users/me/cooperation",
  members: [
    { member_id: "m-claude", label: "Claude · iris", engine: "claude" },
    { member_id: "m-codex", label: "Codex · nyx", engine: "codex" },
  ],
  messages: [],
  created_at: 10,
  updated_at: 10,
  message_count: 0,
};

// ---- non-room frames are passed through --------------------------------
assert.equal(applyRoomEvent({}, { ...base, type: "pong" } as never), null);
assert.equal(applyRoomEvent({}, { ...base, type: "session_list" } as never), null);

// ---- list ---------------------------------------------------------------
let rooms: RoomsState = {};
rooms = applyRoomEvent(rooms, roomList([ROOM]))!;
assert.deepEqual(Object.keys(rooms), ["room-1"]);
assert.equal(rooms["room-1"].name, "cc-remote 协作");
assert.deepEqual(rooms["room-1"].members.map((m) => m.member_id), ["m-claude", "m-codex"]);
assert.equal(rooms["room-1"].members[0].state, undefined, "no run state before dispatch");

// A sidebar summary carries at most the newest entry and must not erase a
// timeline the client already has.
rooms = applyRoomEvent(rooms, {
  ...base, type: "room_message", room_id: "room-1",
  message: { msg_id: "msg-a", ts: 11, author_kind: "user", author_label: "你", text: "@Claude 看下", mentions: ["m-claude"] },
} as never)!;
assert.equal(rooms["room-1"].messages.length, 1);

rooms = applyRoomEvent(rooms, roomList([{
  ...ROOM, messages: [{ msg_id: "msg-a", ts: 11, author_kind: "user", author_label: "你", text: "@Claude 看下", mentions: ["m-claude"] }],
  message_count: 1,
}]))!;
assert.equal(rooms["room-1"].messages.length, 1, "summary must not shrink the timeline");

// ---- message dedupe -----------------------------------------------------
const again = applyRoomEvent(rooms, {
  ...base, type: "room_message", room_id: "room-1",
  message: { msg_id: "msg-a", ts: 11, author_kind: "user", author_label: "你", text: "@Claude 看下", mentions: ["m-claude"] },
} as never)!;
assert.equal(again["room-1"].messages.length, 1, "duplicate msg_id must not duplicate");

// ---- member state -------------------------------------------------------
rooms = applyRoomEvent(rooms, {
  ...base, type: "room_state", room_id: "room-1", member_id: "m-claude",
  session_id: "sess-9", state: "running", queued: 0, started_at: 100,
} as never)!;
const claude = rooms["room-1"].members.find((m) => m.member_id === "m-claude")!;
assert.equal(claude.state, "running");
assert.equal(claude.session_id, "sess-9");
assert.equal(claude.started_at, 100);
assert.equal(rooms["room-1"].members[1].state, undefined, "other members untouched");

assert.deepEqual(runningMembers(rooms["room-1"]).map((m) => m.member_id), ["m-claude"]);
assert.equal(runningSeconds(claude, 124_000), 24, "24s elapsed");

rooms = applyRoomEvent(rooms, {
  ...base, type: "room_state", room_id: "room-1", member_id: "m-claude",
  session_id: "sess-9", state: "done", queued: 0, started_at: null,
} as never)!;
assert.equal(rooms["room-1"].members[0].state, "done");
assert.equal(runningSeconds(rooms["room-1"].members[0], 200_000), null);

// ---- snapshot replaces the window --------------------------------------
rooms = applyRoomEvent(rooms, {
  ...base, type: "room_snapshot", request_id: "r2",
  room: {
    ...ROOM,
    messages: [
      { msg_id: "msg-1", ts: 10, author_kind: "user", author_label: "你", text: "先别动", mentions: [] },
      { msg_id: "msg-2", ts: 12, author_kind: "member", author_label: "Claude · iris", text: "好的", mentions: [], state: "done" },
    ],
    message_count: 2,
    updated_at: 12,
  },
  states: [],
} as never)!;
assert.deepEqual(rooms["room-1"].messages.map((m) => m.msg_id), ["msg-1", "msg-2"]);
assert.equal(rooms["room-1"].messages[1].author_kind, "member");

// ---- unknown room frames are ignored -----------------------------------
assert.equal(applyRoomEvent(rooms, {
  ...base, type: "room_message", room_id: "nope",
  message: { msg_id: "x", ts: 1, author_kind: "user", author_label: "你", text: "hi", mentions: [] },
} as never)!["room-1"] !== undefined, true);

// ---- delete -------------------------------------------------------------
const afterDelete = applyRoomEvent(rooms, { ...base, type: "room_invalidated", room_id: "room-1" } as never)!;
assert.deepEqual(Object.keys(afterDelete), []);

// ---- ordering and filtering --------------------------------------------
const two: RoomsState = {
  a: { ...ROOM, room_id: "a", name: "架构讨论", cwd: "/w/architecture", updated_at: 5 },
  b: { ...ROOM, room_id: "b", name: "发布检查", cwd: "/w/release", updated_at: 9 },
};
assert.deepEqual(roomOrder(two).map((r) => r.room_id), ["b", "a"], "newest activity first");
assert.deepEqual(filterRooms(two, "release").map((r) => r.room_id), ["b"], "matches cwd");
assert.deepEqual(filterRooms(two, "架构").map((r) => r.room_id), ["a"], "matches name");
assert.deepEqual(filterRooms(two, "  ").map((r) => r.room_id), ["b", "a"], "blank query lists all");

// ---- structured card data (L1.1) ---------------------------------------
rooms = applyRoomEvent(rooms, {
  ...base, type: "room_snapshot", request_id: "r3",
  room: {
    ...ROOM,
    room_id: "room-plan",
    messages: [{
      msg_id: "msg-plan", ts: 20, author_kind: "member", author_label: "Claude · iris",
      text: "建议如下。", mentions: [], state: "done",
      plan: { summary: "先加集成测试", steps: ["补 fixture", "覆盖主流程"],
              risks: ["用例不稳定"], needs: ["确认目录"] },
    }],
    message_count: 1,
  },
  states: [],
} as never)!;
const plan = rooms["room-plan"].messages[0].plan!;
assert.equal(plan.summary, "先加集成测试");
assert.deepEqual(plan.steps, ["补 fixture", "覆盖主流程"]);
assert.deepEqual(plan.risks, ["用例不稳定"]);
assert.deepEqual(plan.needs, ["确认目录"]);

// An empty or malformed plan is dropped rather than rendered as an empty card.
rooms = applyRoomEvent(rooms, {
  ...base, type: "room_snapshot", request_id: "r4",
  room: {
    ...ROOM,
    room_id: "room-noplan",
    messages: [{ msg_id: "msg-noplan", ts: 21, author_kind: "member",
                 author_label: "Codex · nyx", text: "普通回答", mentions: [], state: "done",
                 plan: { summary: "", steps: [], risks: [], needs: [] } }],
    message_count: 1,
  },
  states: [],
} as never)!;
assert.equal(rooms["room-noplan"].messages[0].plan, undefined);

// Member model/effort survive a snapshot.
rooms = applyRoomEvent(rooms, {
  ...base, type: "room_snapshot", request_id: "r5",
  room: { ...ROOM, room_id: "room-model", members: [
    { member_id: "m-claude", label: "Claude · iris", engine: "claude",
      model: "claude-opus-5-5", effort: "high" },
  ] },
  states: [],
} as never)!;
assert.equal(rooms["room-model"].members[0].model, "claude-opus-5-5");
assert.equal(rooms["room-model"].members[0].effort, "high");

console.log("rooms-state.test.ts OK");
