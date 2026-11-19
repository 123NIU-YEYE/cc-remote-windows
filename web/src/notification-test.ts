export interface NotificationTestEnvironment {
  permission: NotificationPermission | "unsupported";
  registration: () => Promise<Pick<ServiceWorkerRegistration, "showNotification">>;
  timeoutMs?: number;
}

/** API success means the display request was accepted, not that a toast appeared. */
export async function testDesktopNotification(environment: NotificationTestEnvironment): Promise<string> {
  if (environment.permission === "unsupported") return "当前浏览器不支持系统通知。";
  if (environment.permission === "denied") return "站点通知已被阻止，请在浏览器的站点设置中允许通知。";
  if (environment.permission !== "granted") return "请先开启通知并允许浏览器的权限请求。";
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("timeout")), environment.timeoutMs ?? 8_000);
    });
    await Promise.race([
      environment.registration().then(registration => registration.showNotification("cc-remote 测试通知", {
        body: "任务完成或等待回答时，会通过这里提醒你。点击可返回网页。",
        icon: "/icon-192.png", badge: "/favicon.svg",
        tag: "cc-remote-notification-test", data: { url: "/" },
      })),
      timeout,
    ]);
    return "已请求系统显示测试通知。若未弹出，请检查站点通知权限、浏览器后台设置及系统勿扰模式。";
  } catch {
    return "测试通知未能提交。请确认网页已完整加载，刷新后重试。";
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}
