import type { SessionControl } from "./protocol.ts";
import { withRequestDeadline } from "./session-auth.ts";

export interface StartupReplayState {
  cursors: Record<string, number>;
  generations: Record<string, string>;
  controls: Record<string, SessionControl>;
}

/** Cache is optional: a blocked IndexedDB or lazy chunk must not block the socket.
 * Late results are deliberately ignored; seeding after live traffic can roll a
 * cursor or control watermark backward.
 */
export async function loadStartupReplayState(
  load: () => Promise<StartupReplayState> = () =>
    import("./cache.ts").then(module => module.loadAllReplayState()),
  budgetMs = 250,
): Promise<StartupReplayState> {
  const empty = { cursors: {}, generations: {}, controls: {} };
  try {
    return await withRequestDeadline(() => load(), budgetMs);
  } catch {
    return empty;
  }
}
