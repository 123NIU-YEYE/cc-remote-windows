import type { RemoteNotificationMode } from "./notification-mode";
import { bridge, publish } from "./harmony-push.ts";
let cachedToken = "";
async function request(path: string, body?: object): Promise<Response> {
  return fetch(`/api/push/harmony/${path}`, {
    credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(12000),
    ...(body ? { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) } : {}),
  });
}
async function checkConfig(): Promise<void> {
  const response = await request("config");
  if (!response.ok || (await response.json()).enabled !== true) {
    throw new Error("服务端尚未配置鸿蒙推送，请先完成 Push Kit 配置。");
  }
}
function errorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  // Never surface provider exception objects/tokens from the native bridge.
  if (message === "push_not_configured") return "当前安装包尚未开通 Push Kit，请更新推送签名后安装。";
  if (message === "permission_denied") return "通知权限未开启，请在系统设置中允许 cc-remote 通知。";
  if (message.startsWith("服务端尚未配置")) return message;
  return "鸿蒙推送连接失败，请检查网络后重试。";
}
export async function requestHarmonyPermission(): Promise<boolean> {
  try {
    await checkConfig();
    const result = await bridge()?.request("enable");
    if (!result?.enabled) throw new Error("permission_denied");
    publish({ error: "" });
    return true;
  } catch (error) {
    publish({ error: errorMessage(error) });
    return false;
  }
}
export async function enableHarmonyPush(machineId: string, mode: RemoteNotificationMode): Promise<boolean> {
  try {
    await checkConfig();
    const result = await bridge()?.request("token");
    if (!result?.token) throw new Error("push_not_configured");
    // Re-read on every foreground/rebind so a provider token rotation is repaired.
    const previous = cachedToken;
    cachedToken = result.token;
    const response = await request("subscribe", {
      machine_id: machineId, notification_mode: mode, token: cachedToken,
    });
    if (!response.ok) throw new Error("subscribe_failed");
    if (previous && previous !== cachedToken) {
      await request("unsubscribe", { token: previous }).catch(() => {});
    }
    publish({ error: "" });
    return true;
  } catch (error) {
    publish({ error: errorMessage(error) });
    return false;
  }
}
export async function disableHarmonyPush(): Promise<boolean> {
  try {
    if (cachedToken) await request("unsubscribe", { token: cachedToken }).catch(() => {});
    // Provider invalidation also covers an expired login or unreachable Relay.
    await bridge()?.request("delete");
    cachedToken = "";
    publish({ error: "" });
    return true;
  } catch (error) {
    publish({ error: errorMessage(error) });
    return false;
  }
}
