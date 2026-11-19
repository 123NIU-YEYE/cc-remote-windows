import assert from "node:assert/strict";
import { testDesktopNotification } from "../src/notification-test.ts";

let shown = 0;
const registration = async () => ({ showNotification: async () => { shown += 1; } });
for (const permission of ["unsupported", "default", "denied"] as const) {
  const result = await testDesktopNotification({ permission, registration });
  assert.ok(result);
}
assert.equal(shown, 0, "testing never implicitly asks for permission or bypasses a denial");
assert.match(await testDesktopNotification({ permission: "granted", registration }), /已请求系统显示/);
assert.equal(shown, 1);
assert.match(await testDesktopNotification({
  permission: "granted", registration: async () => { throw new Error("unavailable"); },
}), /未能提交/);
assert.match(await testDesktopNotification({
  permission: "granted", registration: () => new Promise(() => {}), timeoutMs: 1,
}), /未能提交/, "an unready Service Worker cannot leave the settings spinner stuck");
console.log("desktop notification test delivery checks passed");
