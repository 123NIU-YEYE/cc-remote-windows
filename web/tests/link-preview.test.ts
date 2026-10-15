import assert from "node:assert/strict";
import { createServer } from "vite";

const icon = "data:image/png;base64,aWNvbg==";
const originalFetch = globalThis.fetch;
const harness = await createServer({
  root: process.cwd(), appType: "custom", logLevel: "silent",
  server: { middlewareMode: true, watch: null },
});
try {
  const { getLinkMetadata, subscribeLinkIcon } = await harness.ssrLoadModule("/src/link-preview.ts") as typeof import("../src/link-preview");
  const requests: { url: string; detail: string }[] = [];
  let releaseIcon!: (response: Response) => void;
  globalThis.fetch = async (_input, init) => {
    const body = JSON.parse(String(init?.body));
    requests.push(body);
    if (body.detail === "icon") return new Promise<Response>((resolve) => { releaseIcon = resolve; });
    return new Response(JSON.stringify({ icon, title: "Documentation" }));
  };
  const seen: string[] = [], other: string[] = [];
  const stop = subscribeLinkIcon("https://example.org/a", (value) => seen.push(value));
  const stopOther = subscribeLinkIcon("https://other.example/b", (value) => other.push(value));
  const automatic = getLinkMetadata("https://example.org/private?a=secret#hash", "icon");
  const duplicate = getLinkMetadata("https://example.org/other", "icon");
  assert.equal(automatic, duplicate);
  assert.deepEqual(requests, [{ url: "https://example.org", detail: "icon" }]);
  await getLinkMetadata("https://example.org/docs", "page");
  assert.deepEqual(seen, [icon], "a mounted sibling updates when a preview discovers the icon");
  assert.deepEqual(other, [], "sharing must stay within the same origin");
  stop(); stopOther();
  releaseIcon(new Response("{}"));
  assert.equal((await automatic).icon, icon, "a late failed icon request cannot overwrite page discovery");
  assert.deepEqual(seen, [icon], "unmounted listeners are released");
  assert.equal((await getLinkMetadata("https://example.org/new", "icon")).icon, icon);
  assert.equal(requests.length, 2, "subsequent links reuse the discovered origin icon");
  const late: string[] = [];
  subscribeLinkIcon("https://example.org/new", (value) => late.push(value))();
  assert.deepEqual(late, [icon], "newly mounted links paint the cached icon immediately");
} finally {
  globalThis.fetch = originalFetch;
  await harness.close();
}
console.log("link preview icon tests passed");
