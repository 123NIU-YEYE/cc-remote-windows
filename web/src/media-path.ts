export function streamingMediaKind(path: string): "video" | "audio" | null {
  if (/\.(?:mp4|webm|mov|m4v|ogv)$/i.test(path)) return "video";
  if (/\.(?:mp3|m4a|aac|wav|flac|ogg)$/i.test(path)) return "audio";
  return null;
}

export function parentFilePath(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const slash = normalized.lastIndexOf("/");
  if (slash < 0) return ".";
  if (slash === 0) return "/";
  return slash === 2 && /^[A-Za-z]:/.test(normalized)
    ? normalized.slice(0, 3) : normalized.slice(0, slash);
}
