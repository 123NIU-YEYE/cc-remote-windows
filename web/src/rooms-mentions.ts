/**
 * `@` completion for the room composer.
 *
 * Typing "@" has to surface the room's agents, because a member's display label
 * ("Claude · iris") contains a space and cannot be typed as one token. The
 * wrapper accepts either segment of the label, so a candidate should insert the
 * shortest segment that identifies exactly one member: normally the engine name,
 * falling back to the agent name when two members share that engine.
 */
import type { RoomMemberView } from "./rooms-state.ts";

export interface MentionOption {
  /** Text inserted after "@". */
  insert: string;
  /** What the picker shows. */
  label: string;
  engine?: string;
  initial: string;
}

const SEPARATORS = /[·/|-]/;

export function labelSegments(label: string): string[] {
  return label.split(SEPARATORS).map((part) => part.trim()).filter(Boolean);
}

/** Shortest handle that addresses exactly one of `members`. */
export function handleFor(member: RoomMemberView, members: RoomMemberView[]): string {
  const segments = labelSegments(member.label);
  if (segments.length === 0) return member.label.trim();
  const head = segments[0];
  const shared = members.filter(
    (other) => other.member_id !== member.member_id
      && labelSegments(other.label)[0]?.toLowerCase() === head.toLowerCase(),
  );
  if (shared.length === 0) return head;
  // Fall back to the agent name (usually the tail), which is what distinguishes
  // two members of the same engine.
  return segments[segments.length - 1] || head;
}

export function initialOf(member: RoomMemberView): string {
  const segments = labelSegments(member.label);
  const tail = segments[segments.length - 1] ?? member.label;
  return (tail.trim() || member.label).slice(0, 1).toUpperCase();
}

/** Candidates for the fragment typed after "@", best-effort ordered. */
export function mentionCandidates(
  members: RoomMemberView[],
  query: string,
): MentionOption[] {
  const needle = query.trim().toLowerCase();
  const options: MentionOption[] = members.map((member) => ({
    insert: handleFor(member, members),
    label: member.label,
    engine: member.engine,
    initial: initialOf(member),
  }));

  // "@全体" is the documented broadcast; offer it next to the names.
  options.push({ insert: "全体", label: "全体成员", initial: "全" });

  if (!needle) return options;

  return options.filter((option) => {
    const haystack = [option.insert, option.label, ...labelSegments(option.label)]
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
}

/**
 * The `@fragment` being typed immediately before `caret`.
 *
 * Returns null when the caret is not inside a mention, which also covers the
 * "@" of an already-completed mention followed by a space.
 */
export function mentionAt(value: string, caret: number): { start: number; query: string } | null {
  const before = value.slice(0, Math.max(0, Math.min(caret, value.length)));
  const match = /(^|\s)@([^\s@]{0,40})$/.exec(before);
  if (!match) return null;
  return { start: before.length - match[2].length - 1, query: match[2] };
}
