import { expect, test } from "../../web/node_modules/@playwright/test/index.mjs";
import { readFileSync } from "node:fs";

const adapter = readFileSync(new URL("../app/src/main/assets/shell.js", import.meta.url), "utf8");
async function install(page, origin = "http://127.0.0.1:4176") {
  await page.evaluate(`${adapter}(${JSON.stringify(origin)});`);
}
async function touch(page, type, x, time, selector = ".thread") {
  await page.locator(selector).evaluate((target, args) => {
    const point = { identifier: 1, target, clientX: args.x, clientY: 280 };
    const touches = args.type === "touchend" ? [] : [point];
    const event = new Event(args.type, { bubbles: true, cancelable: true });
    Object.defineProperties(event, {
      touches: { value: touches }, targetTouches: { value: touches },
      changedTouches: { value: [point] }, timeStamp: { value: args.time },
    });
    target.dispatchEvent(event);
  }, { type, x, time });
}

test("APK adapter preserves continuous sidebar position and fade without filtering the chat", async ({ page }) => {
  await page.goto("/tests/history-browser.html?mobile-sidebar=1");
  await expect(page.locator(".thread")).toBeVisible();
  // An ordinary browser retains the existing look until this APK-only adapter is installed.
  await expect(page.locator(".pane")).toHaveCSS("filter", "blur(0px)");
  await install(page);
  await expect(page.locator(".pane")).toHaveCSS("filter", "none");
  await expect(page.locator(".pane")).toHaveCSS("will-change", "transform");
  let previousFade = 0;
  await touch(page, "touchstart", 40, 1000);
  for (const distance of [20, 70, 150, 240]) {
    await touch(page, "touchmove", 40 + distance, 1000 + distance * 2);
    await expect.poll(() => page.locator(".pane").evaluate(node => node.getBoundingClientRect().x)).toBe(distance);
    await expect(page.locator(".pane")).toHaveCSS("filter", "none");
    const opacity = await page.locator(".scrim-side").evaluate(node => Number(getComputedStyle(node).opacity));
    expect(opacity).toBeGreaterThan(previousFade);
    expect(opacity).toBeLessThan(1);
    previousFade = opacity;
  }
  await touch(page, "touchend", 280, 1600);
  await expect(page.locator(".shell")).toHaveClass(/sidebar-open/);
  await expect(page.locator(".shell")).not.toHaveAttribute("data-sidebar-motion");
  await expect(page.locator(".pane")).toHaveCSS("filter", "none");
  await touch(page, "touchstart", 280, 2000, ".scrim-side");
  await touch(page, "touchmove", 60, 2400, ".scrim-side");
  await touch(page, "touchend", 60, 2600, ".scrim-side");
  await expect(page.locator(".shell")).not.toHaveClass(/sidebar-open/);
  await expect(page.locator(".shell")).not.toHaveAttribute("data-sidebar-motion");
  await expect(page.locator(".pane")).toHaveCSS("filter", "none");
});

test("APK settings joins the real React menu and survives page switches and reopening", async ({ page }) => {
  await page.goto("/tests/history-browser.html?header-menu=1&engine=codex");
  await install(page);
  await install(page); // A repeated page-finished callback must not duplicate the adapter.
  await expect(page.locator("#cc-remote-android-settings-fallback")).toBeHidden();
  for (let cycle = 0; cycle < 2; cycle++) {
    await page.getByRole("button", { name: "更多设置" }).click();
    await expect(page.getByRole("link", { name: /客户端设置/ })).toHaveCount(1);
    await expect(page.getByRole("link", { name: /客户端设置/ })).toHaveAttribute("href", "cc-remote-app://settings");
    await page.getByRole("button", { name: /^通知/ }).click();
    await expect(page.getByRole("link", { name: /客户端设置/ })).toHaveCount(0);
    await page.getByRole("button", { name: "返回设置" }).click();
    await expect(page.getByRole("link", { name: /客户端设置/ })).toHaveCount(1);
    await page.keyboard.press("Escape");
  }
  expect(await page.locator("#cc-remote-android-style").count()).toBe(1);
});

test("settings remains reachable before login without a permanent address bar", async ({ page }) => {
  await page.goto("/tests/history-browser.html?header-menu=1");
  await expect(page.getByRole("button", { name: "更多设置" })).toBeVisible();
  await page.evaluate(() => document.querySelector("#root").replaceChildren());
  await install(page);
  await expect(page.getByRole("link", { name: "客户端设置", exact: true })).toBeVisible();
});

test("adapter ignores another origin and iframe documents", async ({ page }) => {
  await page.goto("/tests/history-browser.html?header-menu=1");
  await install(page, "https://unrelated.example");
  await expect(page.locator("#cc-remote-android-style")).toHaveCount(0);
  await page.evaluate(() => {
    const frame = document.createElement("iframe");
    frame.src = "/tests/history-browser.html?header-menu=1";
    document.body.append(frame);
  });
  await expect.poll(() => page.frames().length).toBe(2);
  const frame = page.frames()[1];
  await frame.waitForURL(/history-browser\.html/);
  await frame.waitForLoadState();
  await frame.evaluate(`${adapter}("http://127.0.0.1:4176");`);
  await expect(frame.locator("#cc-remote-android-style")).toHaveCount(0);
});
