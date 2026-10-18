import { createContext } from "react";

export const RightPanelPresentation = createContext({
  expanded: false,
  covered: false,
  layer: 0,
  chat: false,
  onExpand: () => {},
  onOpenChat: () => {},
  onDock: () => {},
  onDismiss: () => {},
});

/** Preserve view ownership when a provisional native session gets its real ID. */
export function rekeyPanelScope(scope: string | null, oldSid: string, newSid: string): string | null {
  if (!scope) return scope;
  const parts = JSON.parse(scope);
  if (parts[3] !== oldSid) return scope;
  parts[3] = newSid;
  return JSON.stringify(parts);
}
