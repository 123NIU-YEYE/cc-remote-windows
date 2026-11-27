import { withRequestDeadline } from "./session-auth.ts";

export interface MediaSource { machineId: string; sid: string; path: string;
  engine?: "claude" | "codex" | "dsh"; space?: "code" | "work" }
export interface MediaResource { id: string; url: string; name: string; size: number; mediaType: string }

const messages: Record<string, string> = {
  media_device_offline: "文件连接暂不可用，请稍后重试",
  machine_not_authorized: "没有此设备的访问权限",
  media_expired: "文件读取已过期，请重新打开",
  unauthorized: "登录已过期，请重新登录",
  media_capacity: "打开的文件过多，请关闭部分预览后重试",
  viewer_busy: "文件请求较多，请稍后重试",
  resource_unavailable: "文件暂不可用，请重新打开",
};

export async function openMedia(source: MediaSource, download: boolean, signal?: AbortSignal): Promise<MediaResource> {
  return withRequestDeadline(async deadline => {
  const response = await fetch("/api/media/open", {
    method: "POST", credentials: "same-origin", cache: "no-store",
    signal: signal ? AbortSignal.any([signal, deadline]) : deadline,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ machine_id: source.machineId, sid: source.sid, path: source.path, download,
      engine: source.engine ?? null, space: source.space ?? null }),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(messages[value.error]
    ?? (typeof value.error === "string" && !/^[a-z0-9_]+$/.test(value.error)
      ? value.error : "无法读取文件，请重试"));
  // A device may return metadata, never an arbitrary URL for the browser to
  // fetch. Cookies and device authority remain on this exact relay origin.
  if (!/^[a-f0-9]{32}$/.test(value.id) || value.url !== `/api/media/${value.id}`
      || typeof value.name !== "string" || typeof value.mediaType !== "string"
      || !Number.isSafeInteger(value.size) || value.size < 0) {
    throw new Error("文件连接返回了无效数据");
  }
  return value as MediaResource;
  }, 20_000).catch(reason => {
    if (reason instanceof Error && reason.message === "request timed out")
      throw new Error("文件打开超时，请重试");
    throw reason;
  });
}

export function releaseMedia(resource: MediaResource): void {
  void fetch(resource.url, { method: "DELETE", credentials: "same-origin", keepalive: true }).catch(() => {});
}
