import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

/** Stay inside the sheet's theme and focus boundary, outside its scrolling body. */
export function AppearancePopover({ anchor, id, label, role = "dialog", width = 320,
  onClose, children }: {
  anchor: RefObject<HTMLButtonElement | null>;
  id: string;
  label: string;
  role?: "dialog" | "listbox";
  width?: number;
  onClose: (restoreFocus: boolean) => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const host = anchor.current?.closest<HTMLElement>(".theme-picker");
  const [position, setPosition] = useState<{ left: number; top: number; width: number; maxHeight: number }>();
  useLayoutEffect(() => {
    const node = ref.current, trigger = anchor.current;
    if (!node || !trigger || !host) return;
    const viewport = window.visualViewport;
    const place = () => {
      const sheet = host.getBoundingClientRect();
      const target = (trigger.closest(".appearance-color") ?? trigger).getBoundingClientRect();
      const left = Math.max(sheet.left, viewport?.offsetLeft ?? 0) + 12;
      const right = Math.min(sheet.right, (viewport?.offsetLeft ?? 0) + (viewport?.width ?? window.innerWidth)) - 12;
      const top = Math.max(sheet.top, viewport?.offsetTop ?? 0) + 12;
      const bottom = Math.min(sheet.bottom, (viewport?.offsetTop ?? 0) + (viewport?.height ?? window.innerHeight)) - 12;
      const w = Math.max(0, Math.min(width, right - left));
      const maxHeight = Math.max(0, bottom - top);
      const height = Math.min(node.scrollHeight + 2, maxHeight);
      const below = target.bottom + 8;
      const y = below + height <= bottom ? below : target.top - height - 8;
      const next = { left: Math.max(left, Math.min(target.right - w, right - w)) - sheet.left - host.clientLeft,
        top: Math.max(top, Math.min(y, bottom - height)) - sheet.top - host.clientTop, width: w, maxHeight };
      setPosition(old => old && Object.keys(next).every(key => old[key as keyof typeof next] === next[key as keyof typeof next]) ? old : next);
    };
    place();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !node.contains(event.target) && !trigger.contains(event.target)) {
        // Commit a valid short HEX draft before unmounting its blur handler.
        if (document.activeElement instanceof HTMLElement && node.contains(document.activeElement)) document.activeElement.blur();
        close.current(false);
      }
    };
    const focusOutside = (event: FocusEvent) => {
      if (event.target instanceof Node && !node.contains(event.target) && !trigger.contains(event.target)) close.current(false);
    };
    const observer = new ResizeObserver(place);
    observer.observe(node);
    observer.observe(host);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", focusOutside);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    viewport?.addEventListener("resize", place);
    viewport?.addEventListener("scroll", place);
    return () => {
      observer.disconnect();
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("focusin", focusOutside);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      viewport?.removeEventListener("resize", place);
      viewport?.removeEventListener("scroll", place);
    };
  }, [anchor, host, width]);
  const ready = !!position;
  useLayoutEffect(() => {
    const node = ref.current;
    if (!ready || !node) return;
    // Wait until visible. Never focus HEX here and summon the phone keyboard.
    (node.querySelector<HTMLElement>('[aria-selected="true"]')
      ?? node.querySelector<HTMLElement>('button, [tabindex="0"]') ?? node).focus({ preventScroll: true });
  }, [ready]);
  if (!host) return null;
  return createPortal(<div ref={ref} id={id} role={role} aria-label={label} tabIndex={-1}
    className={`appearance-popover appearance-popover-${role}`} style={{ ...position, visibility: position ? "visible" : "hidden" }}
    onKeyDown={event => {
      if (event.isDefaultPrevented() || event.nativeEvent.isComposing) return;
      if (event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); onClose(true);
      } else if (event.key === "Tab") {
        if (role === "listbox") {
          // The parent sheet then moves focus from the trigger to its next control.
          onClose(true);
          return;
        }
        const nodes = [...(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]') ?? [])]
          .filter(node => node.tabIndex >= 0 && node.getClientRects().length > 0);
        const current = nodes.indexOf(document.activeElement as HTMLElement);
        event.preventDefault(); event.stopPropagation();
        nodes[(current + (event.shiftKey ? -1 : 1) + nodes.length) % nodes.length]?.focus();
      }
    }}>{children}</div>, host);
}
