import { createContext, useContext, useEffect, useRef, type HTMLAttributes } from "react";
import { Icon } from "../icons";
import { RightPanelPresentation } from "../right-panel-presentation";
import "./RightPanelPresentation.css";

const InsidePanel = createContext(false);

/** Change presentation in place: editors, media and chat history stay mounted. */
export function PanelFrame({ children, className = "", label, ...props }:
  HTMLAttributes<HTMLDivElement> & { label: string }) {
  const presentation = useContext(RightPanelPresentation);
  const nested = useContext(InsidePanel);
  const expanded = presentation.expanded && !nested;
  const panelRef = useRef<HTMLDivElement>(null);
  const dismissRef = useRef(presentation.onDismiss);
  dismissRef.current = presentation.onDismiss;

  useEffect(() => {
    const panel = panelRef.current;
    if (!expanded || !panel) return;
    const previous = document.activeElement as HTMLElement | null;
    if (!panel.contains(previous)) panel.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
      // Nested pickers/lightboxes own Escape and focus until they close.
      if ([...document.querySelectorAll<HTMLElement>('[role="dialog"], dialog[open]')]
        .some(node => node !== panel && node.getClientRects().length)) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        dismissRef.current();
      } else if (event.key === "Tab") {
        const nodes = [...panel.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, a[href], iframe, [tabindex="0"]',
        )].filter(node => node.getClientRects().length > 0);
        const current = nodes.indexOf(document.activeElement as HTMLElement);
        const next = current < 0 ? (event.shiftKey ? nodes.length - 1 : 0)
          : (current + (event.shiftKey ? -1 : 1) + nodes.length) % nodes.length;
        event.preventDefault();
        event.stopPropagation();
        (nodes[next] ?? panel).focus({ preventScroll: true });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (previous?.isConnected && !panel.contains(previous)) previous.focus({ preventScroll: true });
    };
  }, [expanded]);

  return <InsidePanel.Provider value={true}>
    {expanded && <div className="panel-expanded-backdrop" aria-hidden="true"
      data-lock-horizontal-swipe="true" onClick={presentation.onDismiss} />}
    <div {...props} ref={panelRef}
      className={`${className} right-panel-frame${expanded ? " panel-expanded" : ""}`}
      role={expanded ? "dialog" : props.role}
      aria-modal={expanded || undefined} aria-label={label}
      tabIndex={expanded ? -1 : undefined} data-lock-horizontal-swipe="true">
      {children}
    </div>
  </InsidePanel.Provider>;
}

export function PanelExpandButton({ label }: { label: string }) {
  const { expanded, onExpand, onDock } = useContext(RightPanelPresentation);
  return <button type="button" className="iconbtn panel-expand"
    onClick={expanded ? onDock : onExpand}
    aria-label={expanded ? "收回右侧" : `居中展开${label}`}
    title={expanded ? "收回右侧" : "居中展开 (⌘/Ctrl+Shift+E)"}
    aria-pressed={expanded} aria-keyshortcuts="Meta+Shift+E Control+Shift+E">
    <Icon name={expanded ? "panel-right" : "expand"} />
  </button>;
}
