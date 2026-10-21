import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import type { DshApiClient } from "../dsh-api";
import type { DshItem } from "../protocol";
import { uuid } from "../util";
import "@xterm/xterm/css/xterm.css";

export default function DshTerminal({ sid, api }: { sid: string; api: DshApiClient }) {
  const [items, setItems] = useState<DshItem[]>([]);
  const [target, setTarget] = useState("");
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [stop, setStop] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const reload = async () => {
    const result = await api.read(sid, "terminals");
    if (result.error) throw new Error(result.error);
    setItems(result.items);
  };
  useEffect(() => {
    const abort = new AbortController();
    api.read(sid, "terminals", { signal: abort.signal }).then(result => {
      if (!abort.signal.aborted) { setItems(result.items); setError(result.error ?? ""); }
    }).catch(error => { if (!abort.signal.aborted) setError(error.message); });
    return () => abort.abort();
  }, [api, sid]);
  const control = async (body: Record<string, unknown>) => {
    const result = await api.control(sid, "terminal", JSON.stringify(body));
    if (result.status !== "success") throw new Error(result.text || "终端操作结果未确认，请刷新列表");
    return result;
  };
  const create = async () => {
    setBusy(true); setError("");
    const id = uuid();
    try { await control({ action: "create", id }); await reload(); setTarget(id); }
    catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  };
  const close = async () => {
    setBusy(true); setError("");
    try { await control({ action: "close", id: target }); setTarget(""); setStop(false); await reload(); }
    catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    if (!target || !host.current) return;
    let gone = false, lease = "", timer: ReturnType<typeof setTimeout>;
    let cursor = 0, pendingBytes = 0, writable = false;
    let chain = Promise.resolve();
    const abort = new AbortController();
    const terminal = new Terminal({ cursorBlink: true, fontSize: 13, scrollback: 1000,
      theme: { background: "#10151e", foreground: "#e5eaf3", cursor: "#88afff", selectionBackground: "#496ba86b" } });
    const fit = new FitAddon(); terminal.loadAddon(fit); terminal.open(host.current);
    setConnected(false); setError(""); setStop(false);
    const fail = (error: Error) => {
      if (gone) return;
      writable = false; setConnected(false); setError(`${error.message} 输入不会自动重发。`);
      clearTimeout(timer); abort.abort();
    };
    const input = (value: Record<string, unknown>) => {
      if (gone || !lease || !writable) return;
      const bytes = new TextEncoder().encode(JSON.stringify(value)).byteLength;
      if (pendingBytes + bytes > 65536) { fail(new Error("待发送输入过多，请重新连接")); return; }
      pendingBytes += bytes;
      chain = chain.then(async () => {
        if (gone || !writable) return;
        try {
          const result = await api.control(sid, "terminal", JSON.stringify({ action: "input", lease, value }));
          if (result.status !== "success") throw new Error(result.text || "输入结果未确认");
        } finally { pendingBytes -= bytes; }
      }).catch(fail);
    };
    const onData = terminal.onData(data => {
      // Bound each UTF-8 frame without splitting a Unicode scalar.
      let part = "", size = 0;
      for (const char of data) {
        const n = new TextEncoder().encode(char).length;
        if (size + n > 2048) { input({ type: "write", data: part }); part = ""; size = 0; }
        part += char; size += n;
      }
      if (part) input({ type: "write", data: part });
    });
    let columns = 0, rows = 0;
    const resize = () => {
      if (gone || !host.current?.clientWidth || !host.current.clientHeight) return;
      fit.fit();
      if (writable && (columns !== terminal.cols || rows !== terminal.rows)) {
        columns = Math.min(500, Math.max(2, terminal.cols)); rows = Math.min(200, Math.max(1, terminal.rows));
        input({ type: "resize", cols: columns, rows });
      }
    };
    let resizeFrame = 0;
    const observer = new ResizeObserver(() => { cancelAnimationFrame(resizeFrame); resizeFrame = requestAnimationFrame(resize); }); observer.observe(host.current);
    const write = (text: string) => new Promise<void>(resolve => terminal.write(text, resolve));
    const read = async () => {
      if (gone) return;
      try {
        const result = await api.read(sid, "terminal_output", { query: lease, before_seq: cursor, signal: abort.signal });
        if (gone) return;
        if (result.error) throw new Error(result.error);
        for (const item of result.items) {
          const frame = item.data ?? {};
          if (frame.type === "snapshot") { terminal.reset(); await write(String(frame.screen ?? "")); }
          else if (frame.type === "output") await write(String(frame.data ?? ""));
          if ("writable" in frame) { writable = frame.writable === true; setConnected(writable); }
        }
        if (gone) return;
        cursor = result.next_seq ?? cursor;
        resize();
        timer = setTimeout(read, result.items.length ? 40 : 200);
      } catch (error) { if (!gone && !abort.signal.aborted) fail(error as Error); }
    };
    void api.control(sid, "terminal", JSON.stringify({ action: "attach", id: target })).then(result => {
      if (result.status !== "success") throw new Error(result.text || "终端连接未确认");
      lease = result.request_id;
      if (gone) { void api.control(sid, "terminal", JSON.stringify({ action: "detach", lease })).catch(() => {}); return; }
      terminal.focus(); void read();
    }).catch(error => { if (!gone) fail(error); });
    return () => {
      gone = true; writable = false; clearTimeout(timer); abort.abort();
      observer.disconnect(); cancelAnimationFrame(resizeFrame); onData.dispose(); terminal.dispose();
      if (lease) void api.control(sid, "terminal", JSON.stringify({ action: "detach", lease })).catch(() => {});
    };
  }, [sid, api, target, revision]);
  return <div className="dsh-terminal">
    <div className="dsh-panel-actions"><button disabled={busy} onClick={() => void create()}>新建终端</button><button disabled={busy} onClick={() => void reload().catch(error => setError(error.message))}>刷新列表</button>
      {target && <><button onClick={() => setRevision(value => value + 1)}>重新连接</button><button disabled={busy} onClick={() => setStop(true)}>停止终端</button></>}
    </div>
    <nav aria-label="终端列表">{items.map(item => <button key={item.id} aria-current={target === item.id ? "page" : undefined} onClick={() => setTarget(item.id)}>{item.title} · {item.state === "running" ? "运行中" : "已退出"}</button>)}</nav>
    {error && <p role="alert" className="dsh-feature-error">{error}</p>}
    {stop && <div className="dsh-inline-confirm"><p>停止这个终端及其中的命令？收起面板会保留进程。</p><button onClick={() => setStop(false)}>取消</button><button disabled={busy} onClick={() => void close()}>确认停止</button></div>}
    {target ? <><div className="dsh-terminal-status">{connected ? "已连接" : "只读或未连接"} · 收起面板会保留终端</div><div className="dsh-terminal-screen" ref={host} /></>
      : <p className="dsh-feature-empty">选择终端或新建一个终端。<br />这里的命令由你直接运行。</p>}
  </div>;
}
