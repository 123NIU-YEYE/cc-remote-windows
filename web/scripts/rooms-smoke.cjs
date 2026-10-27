/**
 * Headless smoke test for the collaboration surface.
 *
 * Usage: node scripts/rooms-smoke.cjs <login-password>
 *        CC_REMOTE_BASE=https://... node scripts/rooms-smoke.cjs <password>
 *
 * Exits 0 only when the 协作 surface mounts, a room renders, the timeline has
 * messages and no room-related console error or failed request occurred. The
 * viewer surface (file preview) is unsupported on Windows, so its 503s from
 * /api/viewers/* are filtered out as known noise. Screenshots land next to the
 * script as _shot-rooms.png / _shot-rooms-dark.png.
 *
 * Logs in through the same endpoint the browser uses, opens the 协作 surface and
 * asserts that a room and its timeline actually render. Records WebSocket frame
 * types so a silent "the request never left the browser" failure is visible.
 */
const { chromium } = require("playwright");
const path = require("path");

const BASE = process.env.CC_REMOTE_BASE || "https://armed-further-households-because.trycloudflare.com";
const PASSWORD = process.argv[2] || "";
const OUT = path.resolve(__dirname, "_shot-rooms.png");

(async () => {
  const browser = await chromium.launch({
    channel: "chrome",
    headless: true,
    proxy: { server: "http://127.0.0.1:7897" },
  });
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 900 },
  });

  // Record every outbound/inbound frame type so a missing request is obvious.
  await context.addInitScript(() => {
    window.__wsLog = [];
    const Native = window.WebSocket;
    window.WebSocket = function (url, protocols) {
      const socket = new Native(url, protocols);
      socket.addEventListener("message", (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (typeof msg.type === "string") window.__wsLog.push("in:" + msg.type);
        } catch { /* binary frame */ }
      });
      const send = socket.send.bind(socket);
      socket.send = (data) => {
        try {
          const msg = JSON.parse(data);
          if (typeof msg.type === "string") window.__wsLog.push("out:" + msg.type);
        } catch { /* ignore */ }
        return send(data);
      };
      return socket;
    };
    window.WebSocket.prototype = Native.prototype;
    Object.assign(window.WebSocket, Native);
  });

  const login = await context.request.post(`${BASE}/api/login`, {
    data: { password: PASSWORD },
    headers: { Origin: BASE, "Content-Type": "application/json" },
    failOnStatusCode: false,
  });
  console.log("login status:", login.status());

  const page = await context.newPage();
  const errors = [];
  const failures = [];
  page.on("response", (res) => {
    if (res.status() >= 400) failures.push(`${res.status()} ${res.url()}`);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text().slice(0, 200)}`);
  });

  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(4000);

  const entry = page.locator('button[aria-label="协作房间"]');
  await entry.waitFor({ timeout: 30000 });
  await entry.click();
  // Wait for the actual response instead of a fixed delay: right after a deploy
  // the bundle swap can race the first click and produce a flaky "0 cards".
  await page.waitForFunction(
    () => (window.__wsLog || []).includes("in:room_list"),
    undefined,
    { timeout: 20000 },
  ).catch(() => {});
  await page.waitForTimeout(1500);

  const log = await page.evaluate(() => window.__wsLog || []);
  const roomFrames = log.filter((entry) => entry.includes("room"));
  console.log("ws frames total:", log.length);
  console.log("room frames:", roomFrames.slice(-12));
  console.log("outbound room requests:", log.filter((e) => e.startsWith("out:") && e.includes("room")));

  // The list can arrive a beat after the frame; wait for the first card rather
  // than counting immediately.
  await page.waitForSelector(".rooms-card", { timeout: 20000 }).catch(() => {});
  const cards = page.locator(".rooms-card");
  const cardCount = await cards.count();
  console.log("rooms overlay mounted:", (await page.locator(".rooms-overlay").count()) > 0);
  console.log("room cards:", cardCount);
  if (cardCount > 0) {
    console.log("first card:", (await cards.first().innerText()).replace(/\s+/g, " ").trim());
    await cards.first().click();
    await page.waitForTimeout(3000);
  }

  const bubbles = page.locator(".rooms-msg");
  const bubbleCount = await bubbles.count();
  console.log("timeline messages:", bubbleCount);
  if (bubbleCount > 0) {
    console.log("last message:", (await bubbles.last().innerText()).replace(/\s+/g, " ").trim().slice(0, 120));
  }

  // Manual quoting: tick entries, see the bar, clear it. Composing the actual
  // text is covered by rooms-quote.test.ts; sending here would add noise to the
  // room the user is working in.
  let quoteOk = false;
  if (bubbleCount > 0) {
    const lastBubble = bubbles.last();
    await lastBubble.hover();
    await lastBubble.locator(".rooms-msg-quote").click();
    const bar = page.locator(".rooms-quotebar");
    await bar.waitFor({ timeout: 5000 }).catch(() => {});
    const barText = (await bar.count()) ? (await bar.innerText()).replace(/\s+/g, " ").trim() : "";
    console.log("quote bar:", JSON.stringify(barText));
    if (barText.includes("引用 1 条")) {
      await page.screenshot({ path: OUT.replace(".png", "-quote.png") });
      await page.locator(".rooms-quotebar-clear").click();
      await page.waitForTimeout(200);
      quoteOk = (await page.locator(".rooms-quotebar").count()) === 0;
    }
  }
  console.log("quote bar works:", quoteOk);

  // Handing one message to another member is the advice -> execution path.
  // Only the picker is exercised here: choosing a member would dispatch a real
  // turn (and spend tokens) just to prove a click works.
  let handoffOk = false;
  if (bubbleCount > 0) {
    const lastBubble = bubbles.last();
    await lastBubble.hover();
    await lastBubble.locator(".rooms-msg-handoff").click();
    const picker = lastBubble.locator(".rooms-handoff");
    await picker.waitFor({ timeout: 5000 }).catch(() => {});
    const chips = picker.locator(".rooms-chip");
    const chipCount = await chips.count();
    console.log("handoff targets:", chipCount,
      chipCount ? (await chips.allInnerTexts()).map((t) => t.trim()) : []);
    handoffOk = chipCount > 0;
    await page.screenshot({ path: OUT.replace(".png", "-handoff.png") });
    await lastBubble.locator(".rooms-msg-handoff").click();  // close again
  }
  console.log("handoff picker works:", handoffOk);

  // Typing "@" must offer the room's agents, and picking one must insert it.
  let mentionOk = false;
  const composer = page.locator(".rooms-textarea");
  if (cardCount > 0 && (await composer.count()) > 0) {
    await composer.click();
    await composer.pressSequentially("@", { delay: 40 });
    const picker = page.locator(".rooms-mention");
    await picker.first().waitFor({ timeout: 8000 }).catch(() => {});
    const optionCount = await picker.count();
    const optionLabels = optionCount ? await picker.allInnerTexts() : [];
    console.log("@ options:", optionCount, optionLabels.slice(0, 4).map((t) => t.replace(/\s+/g, " ").trim()));
    await page.screenshot({ path: OUT.replace(".png", "-mention.png") });
    if (optionCount > 0) {
      await picker.first().click();
      await page.waitForTimeout(300);
      const value = await composer.inputValue();
      console.log("composer after pick:", JSON.stringify(value));
      mentionOk = /^@\S+/.test(value);
      await composer.fill("");
    }
  }
  console.log("mention picker works:", mentionOk);

  await page.screenshot({ path: OUT });
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
  await page.waitForTimeout(800);
  await page.screenshot({ path: OUT.replace(".png", "-dark.png") });
  await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
  console.log("errors:", errors.length ? errors.slice(0, 6) : "none");
  console.log("failed responses:", failures.length ? failures.slice(0, 8) : "none");

  // The viewer surface is disabled on this machine (file preview is unsupported
  // on Windows), so its 503s are expected noise and not a rooms failure.
  const relevantFailures = failures.filter((line) => !line.includes("/api/viewers/"));
  // "Failed to load resource" duplicates the response log above, which already
  // filters the viewer noise; counting it twice would hide a real signal.
  const relevantErrors = errors.filter((line) => !line.includes("Failed to load resource"));
  const ok = cardCount > 0 && bubbleCount > 0 && mentionOk && handoffOk && quoteOk
    && relevantErrors.length === 0 && relevantFailures.length === 0;
  console.log(ok ? "RESULT: PASS" : "RESULT: FAIL");
  await browser.close();
  process.exit(ok ? 0 : 1);
})().catch((err) => {
  console.log("RESULT: FAIL", err.message);
  process.exit(1);
});
