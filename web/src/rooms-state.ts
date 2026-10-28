/**
 * Client-side projection of the collaboration rooms protocol.
 *
 * The rooms live in App-local state rather than the session reducer: a room is
 * not a session, and threading a third surface through the session machinery
 * would touch far more code than the feature needs. These helpers are pure so
 * they can be unit-tested without a browser or a socket.
 */
import type { ServerEvent } from "./protocol.ts";

export type RoomMemberRunState = "idle" | "queued" | "running" | "done" | "failed";

export interface RoomMemberView {
  member_id: string;
  label: string;
  engine: string;
  session_id?: string | null;
  /** Live dispatch state; absent until the member is first addressed. */
  state?: RoomMemberRunState;
  queued?: number;
  started_at?: number | null;
  error?: string | null;
  /** Per-member engine controls (empty = engine default). */
  model?: string | null;
  effort?: string | null;
}

export interface RoomMessageView {
  msg_id: string;
  ts: number;
  author_kind: "user" | "member";
  author_label: string;
  text: string;
  mentions: string[];
  state?: string;
  /** Structured card data a member emitted as a cc-plan block. */
  plan?: RoomPlanView;
}

export interface RoomPlanView {
  summary: string;
  steps: string[];
  risks: string[];
  needs: string[];
}

export interface RoomView {
  room_id: string;
  name: string;
  cwd: string;
  members: RoomMemberView[];
  messages: RoomMessageView[];
  created_at?: number;
  updated_at?: number;
  /** Total timeline length, so the sidebar can show "N 条" while sending only one. */
  message_count?: number;
}

export type RoomsState = Record<string, RoomView>;

const MEMBER_STATES: RoomMemberRunState[] = ["idle", "queued", "running", "done", "failed"];

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asMemberState(value: unknown): RoomMemberRunState | undefined {
  return MEMBER_STATES.includes(value as RoomMemberRunState)
    ? value as RoomMemberRunState
    : undefined;
}

function normalizeMember(raw: unknown): RoomMemberView | null {
  const record = asRecord(raw);
  if (!record) return null;
  const memberId = asString(record.member_id);
  if (!memberId) return null;
  const state = asMemberState(record.state);
  return {
    member_id: memberId,
    label: asString(record.label, memberId),
    engine: asString(record.engine, "claude"),
    session_id: typeof record.session_id === "string" ? record.session_id : null,
    ...(state ? { state } : {}),
    queued: asNumber(record.queued, 0),
    started_at: typeof record.started_at === "number" ? record.started_at : null,
    error: typeof record.error === "string" ? record.error : null,
    model: typeof record.model === "string" ? record.model : null,
    effort: typeof record.effort === "string" ? record.effort : null,
  };
}

function normalizePlan(value: unknown): RoomPlanView | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  const list = (items: unknown): string[] => Array.isArray(items)
    ? items.filter((item): item is string => typeof item === "string" && item.trim() !== "")
    : [];
  const plan: RoomPlanView = {
    summary: asString(record.summary),
    steps: list(record.steps),
    risks: list(record.risks),
    needs: list(record.needs),
  };
  return plan.summary || plan.steps.length || plan.risks.length || plan.needs.length
    ? plan
    : undefined;
}

function normalizeMessage(raw: unknown): RoomMessageView | null {
  const record = asRecord(raw);
  if (!record) return null;
  const msgId = asString(record.msg_id);
  if (!msgId) return null;
  return {
    msg_id: msgId,
    ts: asNumber(record.ts),
    author_kind: record.author_kind === "member" ? "member" : "user",
    author_label: asString(record.author_label),
    text: asString(record.text),
    mentions: Array.isArray(record.mentions)
      ? record.mentions.filter((item): item is string => typeof item === "string")
      : [],
    state: typeof record.state === "string" ? record.state : undefined,
    plan: normalizePlan(record.plan),
  };
}

function normalizeRoom(raw: unknown, previous?: RoomView): RoomView | null {
  const record = asRecord(raw);
  if (!record) return null;
  const roomId = asString(record.room_id);
  if (!roomId) return null;
  const members = Array.isArray(record.members)
    ? record.members.map(normalizeMember).filter((item): item is RoomMemberView => item !== null)
    : [];
  const incoming = Array.isArray(record.messages)
    ? record.messages.map(normalizeMessage).filter((item): item is RoomMessageView => item !== null)
    : [];
  // Sidebar payloads carry only the newest entry; never let a summary erase a
  // timeline the client already has.
  const messages = previous && incoming.length < previous.messages.length
    ? previous.messages
    : incoming;
  const merged = previous
    ? members.map((member) => {
      const before = previous.members.find((item) => item.member_id === member.member_id);
      return before && member.state === undefined
        ? { ...member, state: before.state, started_at: before.started_at, error: before.error }
        : member;
    })
    : members;
  return {
    room_id: roomId,
    name: asString(record.name, roomId),
    cwd: asString(record.cwd),
    members: merged,
    messages,
    created_at: asNumber(record.created_at),
    updated_at: asNumber(record.updated_at),
    message_count: asNumber(record.message_count, messages.length),
  };
}

function appendMessage(room: RoomView, message: RoomMessageView): RoomView {
  if (room.messages.some((item) => item.msg_id === message.msg_id)) return room;
  return {
    ...room,
    messages: [...room.messages, message],
    message_count: (room.message_count ?? room.messages.length) + 1,
    updated_at: message.ts || room.updated_at,
  };
}

/**
 * Fold one server frame into the room map.
 *
 * Returns ``null`` for frames that are not about rooms, so the caller can hand
 * everything else to the session reducer unchanged.
 */
export function applyRoomEvent(rooms: RoomsState, msg: ServerEvent): RoomsState | null {
  switch (msg.type) {
    case "room_list": {
      const next: RoomsState = {};
      const listing = Array.isArray(msg.rooms) ? msg.rooms : [];
      for (const raw of listing) {
        const room = normalizeRoom(raw, rooms[asString(asRecord(raw)?.room_id)]);
        if (room) next[room.room_id] = room;
      }
      // Keep rooms the listing did not mention (another machine's, or a race).
      for (const [id, room] of Object.entries(rooms)) {
        if (!next[id] && listing.every((raw) => asString(asRecord(raw)?.room_id) !== id)) {
          next[id] = room;
        }
      }
      return next;
    }
    case "room_snapshot": {
      const room = normalizeRoom(msg.room, rooms[asString(asRecord(msg.room)?.room_id)]);
      if (!room) return rooms;
      return { ...rooms, [room.room_id]: room };
    }
    case "room_message": {
      const roomId = asString(msg.room_id);
      const room = rooms[roomId];
      const message = normalizeMessage(msg.message);
      if (!room || !message) return rooms;
      return { ...rooms, [roomId]: appendMessage(room, message) };
    }
    case "room_state": {
      const roomId = asString(msg.room_id);
      const room = rooms[roomId];
      if (!room) return rooms;
      const memberId = asString(msg.member_id);
      const state = asMemberState(msg.state);
      const members = room.members.map((member) => member.member_id === memberId
        ? {
          ...member,
          ...(state ? { state } : {}),
          session_id: typeof msg.session_id === "string" ? msg.session_id : member.session_id,
          queued: asNumber(msg.queued, member.queued ?? 0),
          started_at: typeof msg.started_at === "number" ? msg.started_at : null,
          error: typeof msg.error === "string" ? msg.error : null,
        }
        : member);
      return { ...rooms, [roomId]: { ...room, members } };
    }
    case "room_invalidated": {
      const roomId = asString(msg.room_id);
      if (!rooms[roomId]) return rooms;
      const next = { ...rooms };
      delete next[roomId];
      return next;
    }
    default:
      return null;
  }
}

/** Rooms for the sidebar, newest activity first. */
export function roomOrder(rooms: RoomsState): RoomView[] {
  return Object.values(rooms).sort((left, right) =>
    (right.updated_at ?? 0) - (left.updated_at ?? 0));
}

/** Case-insensitive filter over room name and working directory. */
export function filterRooms(rooms: RoomsState, query: string): RoomView[] {
  const needle = query.trim().toLowerCase();
  const ordered = roomOrder(rooms);
  if (!needle) return ordered;
  return ordered.filter((room) =>
    room.name.toLowerCase().includes(needle)
    || room.cwd.toLowerCase().includes(needle));
}

/** Members currently working, for the room header's "N 个处理中" hint. */
export function runningMembers(room: RoomView): RoomMemberView[] {
  return room.members.filter((member) =>
    member.state === "running" || member.state === "queued");
}

/** Seconds a member has been running, for the "处理中 24s" row. */
export function runningSeconds(member: RoomMemberView, now = Date.now()): number | null {
  if (member.state !== "running" || typeof member.started_at !== "number") return null;
  return Math.max(0, Math.round((now / 1000) - member.started_at));
}
