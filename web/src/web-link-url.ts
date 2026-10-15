import { isLocalViewerUrl } from "./remote-viewer";

/** This is only a UI hint. The Relay independently checks DNS and every hop. */
export function previewUrl(value: string): URL | null {
  try {
    const url = new URL(value);
    if (!/^https?:$/.test(url.protocol) || url.username || url.password
        || value.length > 4096 || isLocalViewerUrl(value)
        || !url.hostname.includes(".") || url.hostname.endsWith(".internal")
        || (url.port && url.port !== "80" && url.port !== "443")) return null;
    url.hash = "";
    return url;
  } catch { return null; }
}
