import { parseLocalFileTarget } from "./file-link.ts";

export type PreviewTarget =
  | { kind: "local"; value: string }
  | { kind: "external"; value: string }
  | { kind: "anchor"; value: string }
  | { kind: "blocked"; value: "" };

const SCHEME = /^[A-Za-z][A-Za-z0-9+.-]*:/;
const DRIVE_PATH = /^[A-Za-z]:[\\/]/;

function resolveLocalPath(markdownPath: string, target: string): string | null {
  const normalizedTarget = target.replace(/\\/g, "/");
  const base = markdownPath.replace(/\\/g, "/");
  const drive = DRIVE_PATH.exec(normalizedTarget)?.[0].slice(0, 2)
    ?? DRIVE_PATH.exec(base)?.[0].slice(0, 2);
  const targetAbsolute = normalizedTarget.startsWith("/") || DRIVE_PATH.test(normalizedTarget);
  const baseAbsolute = base.startsWith("/") || DRIVE_PATH.test(base);
  const parts = targetAbsolute
    ? normalizedTarget.replace(/^[A-Za-z]:/, "").split("/")
    : [
        ...base.replace(/^[A-Za-z]:/, "").split("/").slice(0, -1),
        ...normalizedTarget.split("/"),
      ];
  const resolved: string[] = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (resolved.length && resolved[resolved.length - 1] !== "..") {
        resolved.pop();
      } else if (targetAbsolute || baseAbsolute) {
        return null;
      } else {
        // Preserve a relative escape for the authenticated wrapper to resolve.
        // If it leaves cwd, the wrapper uses an exact-file read capability;
        // the browser completes that scoped handshake without a second prompt.
        resolved.push("..");
      }
      continue;
    }
    resolved.push(part);
  }
  if (!resolved.length) return null;
  if (drive) return `${drive}/${resolved.join("/")}`;
  return targetAbsolute || baseAbsolute
    ? `/${resolved.join("/")}`
    : resolved.join("/");
}

/** Resolve a Markdown link/image without ever assigning a local path to an
 * HTML URL. Paths outside cwd remain local targets and are subject to the
 * wrapper's exact-file read capability flow. */
export function classifyPreviewTarget(markdownPath: string, rawTarget: string): PreviewTarget {
  let target = rawTarget.trim();
  if (!target || target.length > 4096) return { kind: "blocked", value: "" };
  if (target.startsWith("#")) return { kind: "anchor", value: target };
  if (/^https?:/i.test(target)) return { kind: "external", value: target };
  if (/^file:\/\//i.test(target)) {
    const file = parseLocalFileTarget(target);
    if (!file) return { kind: "blocked", value: "" };
    target = file.path;
  }
  if (target.startsWith("//") || target.startsWith("\\\\")
      || (SCHEME.test(target) && !DRIVE_PATH.test(target))) {
    return { kind: "blocked", value: "" };
  }

  const pathPart = target.split(/[?#]/, 1)[0];
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathPart);
  } catch {
    return { kind: "blocked", value: "" };
  }
  if (!decoded || decoded.includes("\0")) return { kind: "blocked", value: "" };

  const resolved = resolveLocalPath(markdownPath, decoded);
  if (resolved && new TextEncoder().encode(resolved).length > 4096) {
    return { kind: "blocked", value: "" };
  }
  return resolved
    ? { kind: "local", value: resolved }
    : { kind: "blocked", value: "" };
}

export function isMarkdownPath(path: string): boolean {
  return /\.(?:md|markdown)$/i.test(path.split(/[?#]/, 1)[0]);
}
