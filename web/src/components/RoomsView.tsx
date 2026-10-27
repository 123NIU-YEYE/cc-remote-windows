/**
 * The "协作" surface: rooms on the left, one room's timeline on the right.
 *
 * A room is a working directory plus a set of agents, each of which keeps its own
 * session; typing "@Claude ..." dispatches to that agent's session and its answer
 * comes back into this timeline. The component is presentation-only: every
 * mutation goes through a prop so the socket stays in App.
 */
import { useMemo, useRef, useState } from "react";

import {
  filterRooms, roomOrder, runningMembers, runningSeconds,
  type RoomMemberView, type RoomView, type RoomsState,
} from "../rooms-state";
import { mentionAt, mentionCandidates } from "../rooms-mentions";
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
  /** 引导: append an instruction to that member's running turn. */
  onSteer: (roomId: string, memberId: string, text: string) => void;
  /** Interrupt that member's running turn. */
  onCancel: (roomId: string, memberId: string) => void;
  /** 跳转会话: leave the room and open the member's own session. */
  onOpenSession: (member: RoomMemberView) => void;
  onRenameRoom: (roomId: string, name: string) => void;
  /** Invite or remove members: the full replacement list. */
  onUpdateMembers: (roomId: string, members: RoomMemberDraft[]) => void;
  /** Re-target the room's working directory. */
  onChangeCwd: (roomId: string, cwd: string) => void;
  onDeleteRoom: (roomId: string) => void;
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
  room, active, onSelect, onRename, onDelete, onCwd,
}: {
  room: RoomView;
  active: boolean;
  onSelect: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
  onCwd: (cwd: string) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [editingCwd, setEditingCwd] = useState(false);
  const [draftCwd, setDraftCwd] = useState(room.cwd);
  const [draftName, setDraftName] = useState(room.name);
  const busy = runningMembers(room).length;

  const commitRename = () => {
    const name = draftName.trim();
    if (name && name !== room.name) onRename(name);
    setRenaming(false);
  };

  return (
    <div className={`rooms-card${active ? " is-active" : ""}`}>
      <div className="rooms-card-head">
        {renaming ? (
          <input
            className="rooms-input rooms-rename"
            autoFocus
            value={draftName}
            onChange={(event) => setDraftName(event.target.value)}
            onBlur={commitRename}
            onKeyDown={(event) => {
              if (event.key === "Enter") commitRename();
              if (event.key === "Escape") {
                setDraftName(room.name);
                setRenaming(false);
              }
            }}
          />
        ) : (
          <button type="button" className="rooms-card-open" onClick={onSelect}>
            <span className="rooms-card-name">{room.name}</span>
            {active && <span className="rooms-card-current">当前</span>}
            {busy > 0 && <span className="rooms-card-busy">{busy} 个处理中</span>}
          </button>
        )}
        <span className="rooms-card-tools">
          <button type="button" title="重命名" onClick={() => setRenaming(true)}>改名</button>
          <button type="button" title="修改工作目录" onClick={() => setEditingCwd(true)}>目录</button>
          <button
            type="button"
            title="删除房间（成员会话会保留）"
            onClick={() => {
              if (window.confirm(`删除房间「${room.name}」？成员的会话会保留。`)) onDelete();
            }}
          >
            删除
          </button>
        </span>
      </div>
      <button type="button" className="rooms-card-body" onClick={onSelect}>
        <span className="rooms-card-path">{room.cwd}</span>
        <span className="rooms-card-members">
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
        </span>
      </button>
      {editingCwd && (
        <input
          className="rooms-input rooms-rename"
          autoFocus
          value={draftCwd}
          onChange={(event) => setDraftCwd(event.target.value)}
          onBlur={() => {
            if (draftCwd.trim() && draftCwd !== room.cwd) onCwd(draftCwd.trim());
            setEditingCwd(false);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              if (draftCwd.trim() && draftCwd !== room.cwd) onCwd(draftCwd.trim());
              setEditingCwd(false);
            }
            if (event.key === "Escape") {
              setDraftCwd(room.cwd);
              setEditingCwd(false);
            }
          }}
        />
      )}
    </div>
  );
}
function MemberRow({
  member, roomId, onSteer, onCancel, onOpenSession,
}: {
  member: RoomMemberView;
  roomId: string;
  onSteer: (roomId: string, memberId: string, text: string) => void;
  onCancel: (roomId: string, memberId: string) => void;
  onOpenSession: (member: RoomMemberView) => void;
}) {
  const [steering, setSteering] = useState(false);
  const [steerText, setSteerText] = useState("");
  const running = member.state === "running" || member.state === "queued";
  const label = member.state === "running" ? "处理中"
    : member.state === "queued" ? "排队中"
      : member.state === "failed" ? "失败"
        : member.state === "done" ? "已完成" : "空闲";

  const submitSteer = () => {
    const text = steerText.trim();
    if (!text) return;
    onSteer(roomId, member.member_id, text);
    setSteerText("");
    setSteering(false);
  };

  return (
    <div className="rooms-member" data-state={member.state ?? "idle"}>
      <div className="rooms-member-row">
        <span className={`rooms-avatar rooms-avatar-${member.engine}`}>
          {memberInitial(member)}
        </span>
        <span className="rooms-member-label">{member.label}</span>
        <span className="rooms-member-state">{label}</span>
        <Clock member={member} />
        {member.queued ? <span className="rooms-member-queue">队列 · {member.queued}</span> : null}
        {member.error ? <span className="rooms-member-error">{member.error}</span> : null}
        <span className="rooms-member-actions">
          <button type="button" onClick={() => setSteering((value) => !value)}>
            引导
          </button>
          {running && (
            <button type="button" onClick={() => onCancel(roomId, member.member_id)}>
              取消
            </button>
          )}
          {member.session_id && (
            <button type="button" onClick={() => onOpenSession(member)}>
              跳转会话 ↗
            </button>
          )}
        </span>
      </div>
      {steering && (
        <div className="rooms-steer">
          <input
            className="rooms-input"
            autoFocus
            placeholder={`引导 ${member.label} 当前任务…（Enter 发送）`}
            value={steerText}
            onChange={(event) => setSteerText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                submitSteer();
              }
              if (event.key === "Escape") setSteering(false);
            }}
          />
        </div>
      )}
    </div>
  );
}
function toDrafts(members: RoomMemberView[]): RoomMemberDraft[] {
  return members.map((member) => ({
    member_id: member.member_id,
    label: member.label,
    engine: member.engine as RoomMemberDraft["engine"],
  }));
}

const ENGINE_LABELS: Record<RoomMemberDraft["engine"], string> = {
  claude: "Claude",
  codex: "Codex",
  dsh: "DSH",
};

function InviteRow({ onInvite, onCancel }: {
  onInvite: (draft: RoomMemberDraft) => void;
  onCancel: () => void;
}) {
  const [engine, setEngine] = useState<RoomMemberDraft["engine"]>("claude");
  const [name, setName] = useState("");

  const submit = () => {
    const suffix = name.trim() || "new";
    onInvite({
      // The id only has to be unique inside this room.
      member_id: `${engine}-${suffix}-${Date.now().toString(36)}`,
      label: `${ENGINE_LABELS[engine]} · ${suffix}`,
      engine,
    });
  };

  return (
    <div className="rooms-invite">
      <select
        className="rooms-input rooms-invite-engine"
        value={engine}
        onChange={(event) => setEngine(event.target.value as RoomMemberDraft["engine"])}
      >
        {(Object.keys(ENGINE_LABELS) as RoomMemberDraft["engine"][]).map((key) => (
          <option key={key} value={key}>{ENGINE_LABELS[key]}</option>
        ))}
      </select>
      <input
        className="rooms-input"
        autoFocus
        placeholder="成员名，如 iris（Enter 邀请）"
        value={name}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") { event.preventDefault(); submit(); }
          if (event.key === "Escape") onCancel();
        }}
      />
      <button type="button" className="rooms-primary" onClick={submit}>邀请</button>
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
  rooms, activeRoomId, defaultCwd, onSelectRoom, onCreateRoom, onPostMessage,
  onSteer, onCancel, onOpenSession, onRenameRoom, onDeleteRoom, onUpdateMembers,
  onChangeCwd,
  onRefresh,
}: RoomsViewProps) {
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [composing, setComposing] = useState(false);
  const [inviting, setInviting] = useState(false);
  const insertMention = (option: { insert: string }) => {
    if (!mentionQuery) return;
    const caret = composerRef.current?.selectionStart ?? draft.length;
    const head = draft.slice(0, mentionQuery.start);
    const tail = draft.slice(caret);
    const next = `${head}@${option.insert} ${tail}`;
    setDraft(next);
    setMentionQuery(null);
    // Put the caret after the inserted mention and the space that follows it.
    const position = head.length + option.insert.length + 2;
    requestAnimationFrame(() => {
      const element = composerRef.current;
      if (!element) return;
      element.focus();
      element.setSelectionRange(position, position);
    });
  };

  const ordered = useMemo(() => roomOrder(rooms), [rooms]);
  const listed = useMemo(() => filterRooms(rooms, query), [rooms, query]);
  const active = activeRoomId ? rooms[activeRoomId] : undefined;

  const [mentionQuery, setMentionQuery] = useState<{ start: number; query: string } | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const mentionOptions = useMemo(
    () => (mentionQuery && active ? mentionCandidates(active.members, mentionQuery.query) : []),
    [mentionQuery, active],
  );
  const mentionOpen = mentionQuery !== null && active !== undefined;

  const updateMention = (value: string, caret: number) => {
    setMentionIndex(0);
    setMentionQuery(mentionAt(value, caret));
  };

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
              onRename={(name) => onRenameRoom(room.room_id, name)}
              onDelete={() => onDeleteRoom(room.room_id)}
              onCwd={(cwd) => onChangeCwd(room.room_id, cwd)}
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
                    {active.members.length > 1 && (
                      <button
                        type="button"
                        className="rooms-chip-remove"
                        title={`移除 ${member.label}（不会删除它的会话）`}
                        onClick={() => onUpdateMembers(active.room_id,
                          toDrafts(active.members.filter((item) => item.member_id !== member.member_id)))}
                      >
                        ×
                      </button>
                    )}
                  </span>
                ))}
                <button
                  type="button"
                  className="rooms-chip rooms-chip-invite"
                  onClick={() => setInviting((value) => !value)}
                >
                  + 邀请
                </button>
              </div>
            </header>
            {inviting && (
              <InviteRow
                onCancel={() => setInviting(false)}
                onInvite={(draft) => {
                  onUpdateMembers(active.room_id, toDrafts([...active.members, draft]));
                  setInviting(false);
                }} />
            )}

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
                <MemberRow key={member.member_id} member={member}
                  roomId={active.room_id} onSteer={onSteer}
                  onCancel={onCancel} onOpenSession={onOpenSession} />
              ))}
            </div>

            <div className="rooms-composer">
              {mentionOpen && (
                <div className="rooms-mentions" role="listbox" aria-label="选择成员">
                  <div className="rooms-mentions-head">@ 谁</div>
                  {mentionOptions.map((option, index) => (
                    <button
                      key={option.insert}
                      type="button"
                      role="option"
                      aria-selected={index === mentionIndex}
                      className={`rooms-mention${index === mentionIndex ? " is-active" : ""}`}
                      onMouseEnter={() => setMentionIndex(index)}
                      onMouseDown={(event) => {
                        // mousedown, not click: the textarea would blur first.
                        event.preventDefault();
                        insertMention(option);
                      }}
                    >
                      {option.engine
                        ? <span className={`rooms-avatar rooms-avatar-${option.engine}`}>{option.initial}</span>
                        : <span className="rooms-avatar rooms-avatar-dsh">全</span>}
                      <span className="rooms-mention-label">{option.label}</span>
                      <span className="rooms-mention-insert">@{option.insert}</span>
                    </button>
                  ))}
                  {mentionOptions.length === 0 && (
                    <div className="rooms-empty">没有匹配的成员</div>
                  )}
                </div>
              )}
              <textarea
                ref={composerRef}
                className="rooms-textarea"
                placeholder="发消息，或输入 @ 选择 Agent…（Ctrl+Enter 发送）"
                value={draft}
                onChange={(event) => {
                  setDraft(event.target.value);
                  updateMention(event.target.value, event.target.selectionStart ?? 0);
                }}
                onClick={(event) => {
                  const target = event.currentTarget;
                  updateMention(target.value, target.selectionStart ?? 0);
                }}
                onBlur={() => setMentionQuery(null)}
                onKeyDown={(event) => {
                  if (mentionOpen) {
                    if (event.key === "ArrowDown") {
                      event.preventDefault();
                      setMentionIndex((value) => (value + 1) % mentionOptions.length);
                      return;
                    }
                    if (event.key === "ArrowUp") {
                      event.preventDefault();
                      setMentionIndex((value) => (value - 1 + mentionOptions.length) % mentionOptions.length);
                      return;
                    }
                    if (event.key === "Enter" || event.key === "Tab") {
                      event.preventDefault();
                      if (mentionOptions[mentionIndex]) insertMention(mentionOptions[mentionIndex]);
                      return;
                    }
                    if (event.key === "Escape") {
                      event.preventDefault();
                      setMentionQuery(null);
                      return;
                    }
                  }
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
