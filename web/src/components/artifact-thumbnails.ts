import { useCallback, useEffect, useReducer, useRef, type RefObject } from "react";
import type { Engine, FilePreview, SessionArtifactInfo, Space } from "../protocol";
import type { RelayWs } from "../ws";
import { uuid } from "../util";

/** Visible rows only, two in flight, bounded memory scoped to this panel instance. */
export function useArtifactThumbnails(root: RefObject<HTMLDivElement | null>, entries: SessionArtifactInfo[],
  enabled: boolean, ws: RelayWs | null, sid: string, engine: Engine, space: Space) {
  const cache = useRef(new Map<string, string | null>());
  const receiver = useRef<((message: FilePreview) => boolean) | null>(null);
  const [, repaint] = useReducer(n => n + 1, 0);
  const [generation, refresh] = useReducer(n => n + 1, 0);
  const receive = useCallback((message: FilePreview) => receiver.current?.(message) ?? false, []);
  // Catalog refreshes with identical rows must not restart in-flight image reads.
  const images = JSON.stringify(entries.filter(e => e.kind === "image" && e.available)
    .map(e => [e.id, e.path]));

  useEffect(() => {
    const container = root.current;
    if (!enabled || !container || !ws) return;
    const paths = new Map<string, string>(JSON.parse(images));
    const visible = new Set<string>();
    const pending = new Map<string, { id: string; timer: ReturnType<typeof setTimeout> }>();
    const attempted = new Set<string>();
    const pump = () => {
      for (const id of visible) {
        if (pending.size >= 2) break;
        if (cache.current.has(id) || attempted.has(id)) continue;
        attempted.add(id);
        const requestId = uuid();
        if (!ws.sendGetSessionArtifactPreview(sid, engine, space, id, requestId, "thumbnail")) continue;
        pending.set(requestId, { id, timer: setTimeout(() => {
          pending.delete(requestId);
          pump();
        }, 30_000) });
      }
    };
    receiver.current = message => {
      const request = pending.get(message.request_id);
      if (!request || message.sid !== sid) return false;
      clearTimeout(request.timer);
      pending.delete(message.request_id);
      const valid = !message.error && message.path === paths.get(request.id)
        && message.format === "image" && message.media_type === "image/webp"
        && message.data && message.data.length <= 88_000;
      cache.current.set(request.id, valid ? `data:image/webp;base64,${message.data}` : null);
      // Successful entries can be requested again if the bounded cache evicts them.
      if (valid) attempted.delete(request.id);
      while (cache.current.size > 96) cache.current.delete(cache.current.keys().next().value!);
      repaint();
      pump();
      return true;
    };
    const observer = new IntersectionObserver(changes => {
      for (const change of changes) {
        const id = (change.target as HTMLElement).dataset.artifactImage!;
        if (change.isIntersecting) visible.add(id);
        else visible.delete(id);
      }
      pump();
    }, { root: container });
    container.querySelectorAll<HTMLElement>("[data-artifact-image]").forEach(row => {
      if (paths.has(row.dataset.artifactImage!)) observer.observe(row);
    });
    return () => {
      observer.disconnect();
      receiver.current = null;
      for (const request of pending.values()) clearTimeout(request.timer);
    };
  }, [root, images, enabled, ws, sid, engine, space, generation]);

  return { sources: cache.current, receive, refresh: () => { cache.current.clear(); refresh(); } };
}
