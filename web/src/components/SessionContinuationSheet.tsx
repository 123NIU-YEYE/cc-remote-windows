import { useState, type RefObject } from "react";
import type { Engine, SessionControl } from "../protocol";
import { presentSessionControl } from "../session-control-ui";
import { continuationCommand, nativeContinuationId } from "../session-continuation";
import { CenteredSheet } from "./CenteredSheet";
import "./SessionContinuationSheet.css";

export default function SessionContinuationSheet({ engine, sessionId, cwd, machine, account,
  control, connected, busy, archived, dshConnected, onClose, trigger }: {
  engine: Engine; sessionId: string | null; cwd: string | null; machine: string; account: string;
  control?: SessionControl | null; connected: boolean; busy: boolean; archived: boolean;
  dshConnected: boolean; onClose: () => void; trigger: RefObject<HTMLElement | null>;
}) {
  const [notice, setNotice] = useState("");
  const id = nativeContinuationId(engine, sessionId);
  const command = continuationCommand(engine, id, cwd);
  const nativeClaude = engine === "claude" && control?.control_mode === "external_cli";
  const blocked = archived || !connected || (engine === "claude"
    && (busy || nativeClaude || !control || presentSessionControl(control).locked));
  const status = !connected ? "离线：控制权状态待重连确认"
    : engine === "dsh" ? (dshConnected ? "已连接 DSH 桌面服务" : "DSH 桌面连接待确认")
      : control ? presentSessionControl(control).title : "控制权待同步确认";
  const copy = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setNotice(`已复制${label}`);
    } catch {
      setNotice("浏览器未允许复制，请选中下方文本手动复制。");
    }
  };
  return <CenteredSheet open label="会话信息与本地续接" onClose={onClose}
    returnFocusRef={trigger} maxWidth={620}>
    <div className="sheet-scroll continuation-body">
      <dl>
        <div><dt>设备</dt><dd>{machine}</dd></div>
        <div><dt>当前账号</dt><dd>{account}</dd></div>
        <div><dt>控制权</dt><dd role="status">{status}</dd></div>
      </dl>
      <label>原生会话 ID<textarea readOnly aria-label="原生会话 ID" value={id ?? "等待引擎分配正式 ID"} /></label>
      <button type="button" disabled={!id} onClick={() => id && void copy(id, "会话 ID")}>复制会话 ID</button>
      <label>原工作目录<textarea readOnly aria-label="原工作目录" value={cwd ?? "尚未确认"} /></label>
      <button type="button" disabled={!cwd} onClick={() => cwd && void copy(cwd, "工作目录")}>复制工作目录</button>
      {archived && <p>当前会话已归档，请先恢复会话再续接。</p>}
      {engine === "claude" && <p>Claude 保持单写入者：先等当前回合结束。在原目录恢复同一 UUID；本地终端持有会话时，网页会只读镜像。切回网页应先退出终端或明确移交控制权。</p>}
      {nativeClaude && <p>本机会话已经打开，请返回持有会话的那个终端继续。</p>}
      {engine === "codex" && <p>在这台设备、使用当前账号的终端执行。普通终端与网页应连接同一个官方 daemon；桌面 App 需从 Codex Shared 入口启动并单独确认连接。</p>}
      {engine === "dsh" && <p>在这台设备的已配对 DSH 桌面中选择上面的原生会话。网页 ID 的 dsh@ 前缀只用于路由，桌面记录仍使用原生 ID。</p>}
      {command && <>
        <label>当前账号的本地续接命令<textarea readOnly aria-label="本地续接命令" value={command} rows={4} /></label>
        <p>先确认终端已选择上方账号。命令保留原目录和原生 ID，不切换账号、不包含凭据，也不会从网页自动执行。</p>
        <button type="button" disabled={blocked} onClick={() => void copy(command, "续接命令")}>复制续接命令</button>
      </>}
      <p role="status" aria-live="polite">{notice}</p>
    </div>
  </CenteredSheet>;
}
