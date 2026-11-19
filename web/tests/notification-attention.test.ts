import assert from "node:assert/strict";
import { needsDesktopNotification } from "../src/notification-attention.ts";

const context = { engine: "claude", space: "code" };
const reading = { sid: "s", engine: "claude", space: "code", hidden: false, focused: true };
assert.equal(needsDesktopNotification("s", context, reading), false);
assert.equal(needsDesktopNotification("s", context, { ...reading, focused: false }), true);
assert.equal(needsDesktopNotification("s", context, { ...reading, hidden: true }), true);
assert.equal(needsDesktopNotification("other", context, reading), true);
assert.equal(needsDesktopNotification("s", { ...context, engine: "codex" }, reading), true);
assert.equal(needsDesktopNotification("s", { ...context, space: "work" }, reading), true);
assert.equal(needsDesktopNotification("side", { ...context, parent_session_id: "s" }, reading), false);
console.log("desktop notification attention checks passed");
