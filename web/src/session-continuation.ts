import type { Engine } from "./protocol";

export function nativeContinuationId(engine: Engine, value: string | null): string | null {
  const id = engine === "dsh" ? value?.replace(/^dsh@/, "") : value;
  return id && !id.startsWith("tmp-") && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)
    ? id : null;
}

/** Generate text for review/copy only. Never execute a command or select an account. */
export function continuationCommand(engine: Engine, id: string | null, cwd: string | null): string | null {
  const native = nativeContinuationId(engine, id);
  if (!native || engine === "dsh" || !cwd
    || [...cwd].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) return null;
  const windows = /^[A-Za-z]:[\\/]/.test(cwd) || /^\\\\/.test(cwd);
  if (!windows && !cwd.startsWith("/")) return null;
  const quote = windows
    ? (text: string) => `'${text.replace(/'/g, "''")}'`
    : (text: string) => `'${text.replace(/'/g, "'\\''")}'`;
  const resume = `${engine === "codex" ? "codex resume" : "claude --resume"} ${quote(native)}`;
  return windows
    ? `& {\n  Set-Location -LiteralPath ${quote(cwd)} -ErrorAction Stop\n  ${resume}\n}`
    : `cd -- ${quote(cwd)} &&\n  ${resume}`;
}
