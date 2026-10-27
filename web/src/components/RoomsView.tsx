/**
 * The "协作" surface: rooms on the left, one room's timeline on the right.
 *
 * A room is a working directory plus a set of agents, each of which keeps its own
 * session; typing "@Claude ..." dispatches to that agent's session and its answer
 * comes back into this timeline. The component is presentation-only: every
 * mutation goes through a prop so the socket stays in App.
 */
import { useMemo, useState } from "react";

import {
  filterRooms, roomOrder, runningMembers, runningSeconds,
  type RoomMemberView, type RoomView, type RoomsState,
} from "../rooms-state";
import "./RoomsView.css";

export interface RoomMemberDraft {
  member_id: string;
  label: string;
  engine: "claude" | "codex" | "dsh";
}

export interface RoomsViewProps {
  rooms: RoomsState;
  activeRoomId: string | null;
  /** Default directory for a new room (the current session's cwd). */
  defaultCwd: string;
  onSelectRoom: (roomId: string) => void;
  onCreateRoom: (name: string, cwd: string, members: RoomMemberDraft[]) => void;
  onPostMessage: (roomId: string, text: string) => void;
  onRefresh: () => void;
}

function memberInitial(member: RoomMemberView): string {
  const tail = member.label.split(/[·/|-]/).pop() ?? member.label;
  return (tail.trim() || member.label).slice(0, 1).toUpperCase();
}

function Clock({ member }: { member: RoomMemberView }) {
  // Re-render only needs coarse accuracy; the parent re-renders on every frame.
  const seconds = runningSeconds(member, Date.now());
  if (seconds === null) return null;
  return <span className="rooms-clock">{seconds}s</span>;
}

function RoomCard({
  room, active, onSelect,
}: { room: RoomView; active: boolean; onSelect: () => void }) {
  const busy = runningMembers(room).length;
  return (
    <button
      type="button"
      className={`rooms-card${active ? " is-active" : ""}`}
      onClick={onSelect}
    >
      <div className="rooms-card-head">
        <span className="rooms-card-name">{room.name}</span>
        {active && <span className="rooms-card-current">当前</span>}
        {busy > 0 && <span className="rooms-card-busy">{busy} 个处理中</span>}
      </div>
      <div className="rooms-card-path">{room.cwd}</div>
      <div className="rooms-card-members">
        {room.members.map((member) => (
          <span
            key={member.member_id}
            className={`rooms-avatar rooms-avatar-${member.engine}`}
            title={member.label}
            data-state={member.state ?? "idle"}
          >
            {memberInitial(member)}
          </span>
        ))}
        {typeof room.message_count === "number" && room.message_count > 0 && (
          <span className="rooms-card-count">{room.message_count} 条</span>
        )}
      </div>
    </button>
  );
}

function MemberRow({ member }: { member: RoomMemberView }) {
  const label = member.state === "running" ? "处理中"
    : member.state === "queued" ? "排队中"
      : member.state === "failed" ? "失败"
        : member.state === "done" ? "已完成" : "空闲";
  return (
    <div className="rooms-member-row" data-state={member.state ?? "idle"}>
      <span className={`rooms-avatar rooms-avatar-${member.engine}`}>
        {memberInitial(member)}
      </span>
      <span className="rooms-member-label">{member.label}</span>
      <span className="rooms-member-state">{label}</span>
      <Clock member={member} />
      {member.queued ? <span className="rooms-member-queue">队列 · {member.queued}</span> : null}
      {member.error ? <span className="rooms-member-error">{member.error}</span> : null}
    </div>
  );
}

function NewRoomForm({ defaultCwd, onSubmit }: {
  defaultCwd: string;
  onSubmit: (name: string, cwd: string, members: RoomMemberDraft[]) => void;
}) {
  const [name, setName] = useState("");
  const [cwd, setCwd] = useState(defaultCwd);
  const [claude, setClaude] = useState(true);
  const [codex, setCodex] = useState(true);

  const submit = () => {
    const members: RoomMemberDraft[] = [];
    if (claude) members.push({ member_id: "claude-1", label: "Claude · iris", engine: "claude" });
    if (codex) members.push({ member_id: "codex-1", label: "Codex · nyx", engine: "codex" });
    if (!name.trim() || !cwd.trim() || members.length === 0) return;
    onSubmit(name.trim(), cwd.trim(), members);
    setName("");
  };

  return (
    <div className="rooms-new">
      <input
        className="rooms-input"
        placeholder="房间名，如「架构讨论」"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <input
        className="rooms-input"
        placeholder="工作目录"
        value={cwd}
        onChange={(event) => setCwd(event.target.value)}
      />
      <div className="rooms-new-members">
        <label>
          <input type="checkbox" checked={claude} onChange={(e) => setClaude(e.target.checked)} />
          Claude
        </label>
        <label>
          <input type="checkbox" checked={codex} onChange={(e) => setCodex(e.target.checked)} />
          Codex
        </label>
        <button type="button" className="rooms-primary" onClick={submit}>创建</button>
      </div>
    </div>
  );
}

export function RoomsView({
  rooms, activeRoomId, defaultCwd, onSelectRoom, onCreateRoom, onPostMessage, onRefresh,
}: RoomsViewProps) {
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [composing, setComposing] = useState(false);

  const ordered = useMemo(() => roomOrder(rooms), [rooms]);
  const listed = useMemo(() => filterRooms(rooms, query), [rooms, query]);
  const active = activeRoomId ? rooms[activeRoomId] : undefined;

  const send = () => {
    const text = draft.trim();
    if (!text || !active) return;
    onPostMessage(active.room_id, text);
    setDraft("");
  };

  return (
    <div className="rooms">
      <aside className="rooms-sidebar">
        <div className="rooms-sidebar-head">
          <span>协作</span>
          <button type="button" className="rooms-icon-button" title="刷新" onClick={onRefresh}>↻</button>
        </div>
        <input
          className="rooms-input rooms-search"
          placeholder="搜索房间、路径…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="rooms-list">
          {listed.map((room) => (
            <RoomCard
              key={room.room_id}
              room={room}
              active={room.room_id === activeRoomId}
              onSelect={() => onSelectRoom(room.room_id)}
            />
          ))}
          {listed.length === 0 && (
            <div className="rooms-empty">
              {ordered.length === 0 ? "还没有房间。新建一个，把 agent 拉进来一起干活。" : "没有匹配的房间。"}
            </div>
          )}
        </div>
        {composing
          ? <NewRoomForm defaultCwd={defaultCwd} onSubmit={(n, c, m) => {
            onCreateRoom(n, c, m);
            setComposing(false);
          }} />
          : (
            <button type="button" className="rooms-primary rooms-new-toggle" onClick={() => setComposing(true)}>
              + 新建房间
            </button>
          )}
      </aside>

      <section className="rooms-detail">
        {active ? (
          <>
            <header className="rooms-detail-head">
              <div>
                <div className="rooms-detail-title">{active.name}</div>
                <div className="rooms-detail-path">{active.cwd}</div>
              </div>
              <div className="rooms-detail-members">
                {active.members.map((member) => (
                  <span key={member.member_id} className={`rooms-chip rooms-chip-${member.engine}`}>
                    {member.label}
                  </span>
                ))}
              </div>
            </header>

            <div className="rooms-timeline">
              {active.messages.map((message) => (
                <div
                  key={message.msg_id}
                  className={`rooms-msg rooms-msg-${message.author_kind}`}
                >
                  <div className="rooms-msg-meta">
                    <span className="rooms-msg-author">{message.author_label}</span>
                    <span className="rooms-msg-time">
                      {new Date(message.ts * 1000).toLocaleTimeString()}
                    </span>
                    {message.state === "failed" && <span className="rooms-badge-fail">失败</span>}
                  </div>
                  <div className="rooms-msg-text">{message.text}</div>
                </div>
              ))}
              {active.messages.length === 0 && (
                <div className="rooms-empty">
                  用 @ 指派任务，例如「@Claude 先整理一下架构思路」。成员各自在自己的会话里干活。
                </div>
              )}
            </div>

            <div className="rooms-members-bar">
              {active.members.map((member) => (
                <MemberRow key={member.member_id} member={member} />
              ))}
            </div>

            <div className="rooms-composer">
              <textarea
                className="rooms-textarea"
                placeholder="发消息，或 @ 某个 Agent…（Ctrl+Enter 发送）"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                    event.preventDefault();
                    send();
                  }
                }}
              />
              <button type="button" className="rooms-primary" onClick={send}>发送</button>
            </div>
          </>
        ) : (
          <div className="rooms-empty rooms-empty-center">
            从左侧选择房间，或新建一个。
          </div>
        )}
      </section>
    </div>
  );
}
