/** The active tab can remain visible while a different desktop app has focus. */
export interface NotificationView {
  sid: string | null;
  engine: string;
  space: string;
  hidden: boolean;
  focused?: boolean;
}

export function needsDesktopNotification(
  sid: string,
  context: { engine: string; space: string; parent_session_id?: string | null },
  view: NotificationView,
): boolean {
  return view.hidden || view.focused === false
    || (context.parent_session_id ?? sid) !== view.sid
    || context.engine !== view.engine || context.space !== view.space;
}
