import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { continuationCommand, nativeContinuationId } from "../src/session-continuation.ts";

assert.equal(continuationCommand("codex", "native-id", "D:\\my folder\\$env:PATH"),
  "& {\n  Set-Location -LiteralPath 'D:\\my folder\\$env:PATH' -ErrorAction Stop\n  codex resume 'native-id'\n}");
assert.equal(continuationCommand("claude", "uuid", "C:\\O'Brien"),
  "& {\n  Set-Location -LiteralPath 'C:\\O''Brien' -ErrorAction Stop\n  claude --resume 'uuid'\n}");
assert.equal(continuationCommand("claude", "uuid", "/home/O'Brien"),
  "cd -- '/home/O'\\''Brien' &&\n  claude --resume 'uuid'");
for (const id of [null, "tmp-123", "bad;command", "other@uuid", "a\nwhoami"]) {
  assert.equal(continuationCommand("codex", id, "D:\\repo"), null);
}
for (const cwd of [null, "relative", "C:\\ok\nwhoami", "\0"]) {
  assert.equal(continuationCommand("codex", "uuid", cwd), null);
}
assert.equal(nativeContinuationId("dsh", "dsh@session-native"), "session-native");
assert.equal(continuationCommand("dsh", "session-native", "D:\\repo"), null);
// Exercise the actual shell: a missing cwd must never fall through to a CLI.
for (const engine of ["codex", "claude"] as const) {
  const command = continuationCommand(engine, "guard-check", join(tmpdir(), randomUUID(), "missing"))!;
  const result = process.platform === "win32"
    ? spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
      `function ${engine} { Write-Output 'UNEXPECTED_CLI_CALL' }; ${command}`], { encoding: "utf8" })
    : spawnSync("sh", ["-c", `${engine}() { printf UNEXPECTED_CLI_CALL; }; ${command}`], { encoding: "utf8" });
  assert.ifError(result.error);
  assert.notEqual(result.status, 0);
  assert.ok(!result.stdout.includes("UNEXPECTED_CLI_CALL"));
}
console.log("Native continuation commands preserve cwd/identity and quote shell input safely");
