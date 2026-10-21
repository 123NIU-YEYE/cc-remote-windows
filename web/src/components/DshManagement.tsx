import { useEffect, useState } from "react";
import type { DshApiClient } from "../dsh-api";
import type { DshItem } from "../protocol";

type Kind = "schedules" | "settings" | "presets" | "bundles";
const names: Record<string, string> = {
  subagent: "子代理", "agent-loop": "代理循环", loop: "循环", "tool-bash": "命令执行",
  shell: "Shell", "web-search": "网络搜索", "agent-preset-registry": "默认预设",
  selectedDefault: "默认预设", maxIterations: "最大循环次数", maxConcurrency: "最大并发数",
  maxSteps: "最大步数", timeout: "超时时间", timeoutMs: "超时（毫秒）", enabled: "启用",
};
const status: Record<string, string> = { active: "生效中", inactive: "已结束", enabled: "已启用", disabled: "未启用", failed: "配置异常", default: "默认", available: "可用" };
const captions = {
  schedules: "任务保留在原来的会话中。投递记录表示消息已送达，不代表模型已完成。",
  settings: "修改 DSH 的原生运行设置。现有会话的生效范围由对应插件决定。",
  presets: "为新会话选择起点。已创建的会话继续使用自己的预设。",
  bundles: "安装和管理 DSH 插件包。需要重启的修改会保留到下次启动。",
};

/** Management writes only happen on a user's explicit Save / Install action. */
export function DshManagement({ sid, kind, api, onCompose }: {
  sid: string; kind: Kind; api: DshApiClient;
  onCompose: (prompt: string, preset?: string) => void;
}) {
  const [items, setItems] = useState<DshItem[]>([]);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<DshItem | null>(null);
  const [spec, setSpec] = useState("");
  const [remove, setRemove] = useState<DshItem | null>(null);
  const [deliveries, setDeliveries] = useState<{ id: string; rows: DshItem[] } | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true); setError("");
    api.read(sid, kind, { signal: abort.signal }).then(result => {
      if (abort.signal.aborted) return;
      setItems(result.items); setError(result.error ?? "");
    }).catch(error => { if (!abort.signal.aborted) setError(error.message); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [sid, kind, api, revision]);
  const mutate = async (body: Record<string, unknown>) => {
    setBusy(true); setNotice("");
    try {
      const result = await api.control(sid, "manage", JSON.stringify(body));
      setNotice(result.text);
      if (result.status === "success") { setEditing(null); setRemove(null); setSpec(""); }
      // Never retry a possibly accepted mutation. Read authoritative state.
      setRevision(value => value + 1);
    } catch (error) { setNotice(`${(error as Error).message}；请刷新核实结果，操作不会自动重发。`); }
    finally { setBusy(false); }
  };
  const makeDefault = async (item: DshItem) => {
    setBusy(true); setNotice("");
    try {
      const result = await api.read(sid, "settings");
      const row = result.items.find(row => row.id === "agent-preset-registry" && row.controllable);
      if (!row) throw new Error(result.error || "当前运行时未开放默认预设设置");
      await mutate({ action: "preset_default", id: item.id, revision: row.data?.revision });
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };
  const history = async (item: DshItem) => {
    if (deliveries?.id === item.id) { setDeliveries(null); return; }
    setBusy(true); setNotice("");
    try {
      const result = await api.read(sid, "schedule_history", { target_sid: item.sid ?? undefined, query: item.id });
      if (result.error) throw new Error(result.error);
      setDeliveries({ id: item.id, rows: result.items });
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };
  return <div className="dsh-management">
    <div className="dsh-management-toolbar">
      <button disabled={loading || busy} onClick={() => setRevision(value => value + 1)}>刷新</button>
      {kind === "schedules" && <button className="dsh-primary" onClick={() => onCompose("请帮我创建一个定时任务。先和我确认任务内容、执行时间和时区，再创建。")}>通过对话创建</button>}
      {kind === "presets" && <button className="dsh-primary" onClick={() => onCompose("我想创建一个 DSH 代理预设，请先和我确认用途及需要的能力。", "cordis")}>创建预设</button>}
    </div>
    <div className="dsh-management-content">
      <p className="dsh-management-caption">{captions[kind]}</p>
      {notice && <p role="status" className="dsh-management-notice">{notice}</p>}
      {error && <p role="alert" className="dsh-feature-error">{error}</p>}
      {kind === "bundles" && <form className="dsh-install" onSubmit={event => { event.preventDefault(); void mutate({ action: "bundle_install", spec }); }}>
        <label htmlFor="dsh-bundle-spec">添加插件包</label><div><input id="dsh-bundle-spec" placeholder="包名或设备上的路径" value={spec} onChange={event => setSpec(event.target.value)} maxLength={2048} />
          <button className="dsh-primary" disabled={busy || !spec.trim()}>安装</button></div>
      </form>}
      {items.map(item => <article className="dsh-management-card" key={item.id}>
        <div className="dsh-management-title"><strong>{names[item.title] ?? item.title}</strong>{status[item.state] && <small>{status[item.state]}</small>}</div>
        {item.detail && <p>{item.detail}</p>}
        {kind === "schedules" && <p className="dsh-management-meta">{scheduleDescription(item.data)} · {item.sid}</p>}
        {kind === "bundles" && <small className="dsh-management-meta">{String(item.data?.version ?? "")}</small>}
        {kind === "settings" ? <SettingsForm key={`${item.id}:${item.data?.revision}`} item={item} busy={busy} onSave={patch => mutate({ action: "settings", ns: item.id, revision: item.data?.revision, patch })} />
          : <div className="dsh-management-actions">
            {kind === "presets" && <><button disabled={busy || item.state === "failed"} onClick={() => onCompose("", item.id)}>用此预设新建会话</button><button disabled={busy || item.state !== "available"} onClick={() => void makeDefault(item)}>设为默认</button></>}
            {kind === "schedules" && <><button disabled={busy} onClick={() => void history(item)}>投递记录</button><button disabled={busy || item.state !== "active"} onClick={() => setEditing(editing?.id === item.id ? null : item)}>编辑</button><button disabled={busy} onClick={() => setRemove(item)}>删除</button></>}
            {kind === "bundles" && item.controllable && <><button disabled={busy} onClick={() => void mutate({ action: item.state === "enabled" ? "bundle_disable" : "bundle_enable", name: item.id })}>{item.state === "enabled" ? "停用" : "启用"}</button>{item.data?.removable === true && <button disabled={busy} onClick={() => setRemove(item)}>卸载</button>}</>}
          </div>}
        {editing?.id === item.id && <ScheduleForm key={editing.id} item={editing} busy={busy} onCancel={() => setEditing(null)} onSave={changes => mutate({ action: "schedule_update", sid: editing.sid, id: editing.id, expected: editing.data, ...changes })} />}
        {remove?.id === item.id && <div className="dsh-inline-confirm"><p>{kind === "bundles" ? "卸载这个插件包？正在使用的插件由 DSH 决定是否允许卸载。" : "删除这个任务？已投递的消息会保留。"}</p><button disabled={busy} onClick={() => setRemove(null)}>取消</button><button disabled={busy} onClick={() => void mutate(kind === "bundles" ? { action: "bundle_remove", name: item.id } : { action: "schedule_delete", sid: item.sid, id: item.id })}>确认{kind === "bundles" ? "卸载" : "删除"}</button></div>}
        {deliveries?.id === item.id && <div className="dsh-deliveries"><strong>最近投递</strong>{deliveries.rows.map(row => <p key={row.id}>{row.title}<small>{row.detail}</small></p>)}{!deliveries.rows.length && <p>暂无保留的投递记录</p>}</div>}
      </article>)}
      {loading && <p role="status" className="dsh-feature-empty">读取中…</p>}
      {!loading && !error && !items.length && <p className="dsh-feature-empty">{kind === "settings" ? "当前运行时没有开放可编辑的执行设置" : "暂无记录"}</p>}
    </div>
  </div>;
}

function SettingsForm({ item, busy, onSave }: { item: DshItem; busy: boolean; onSave: (patch: Record<string, unknown>) => void }) {
  const values = (item.data?.values ?? {}) as Record<string, string | number | boolean | null>;
  const [draft, setDraft] = useState(values);
  const patch = Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== values[key]));
  return <form className="dsh-settings-form" onSubmit={event => { event.preventDefault(); onSave(patch); }}>
    {Object.entries(values).map(([key, value]) => <label key={key}><span>{names[key] ?? key}</span>
      {typeof value === "boolean" ? <input type="checkbox" checked={!!draft[key]} disabled={!item.controllable || busy} onChange={event => setDraft(current => ({ ...current, [key]: event.target.checked }))} />
        : <input type={typeof value === "number" ? "number" : "text"} step="any" value={String(draft[key] ?? "")} disabled={!item.controllable || busy} onChange={event => setDraft(current => ({ ...current, [key]: typeof value === "number" ? Number(event.target.value) : event.target.value }))} />}</label>)}
    {!!Object.keys(patch).length && <button className="dsh-primary" disabled={busy || !item.controllable}>保存修改</button>}
  </form>;
}

function scheduleDescription(data?: Record<string, unknown> | null): string {
  if (!data) return "";
  if (data.kind === "every") return `每 ${data.everySeconds} 秒`;
  if (data.kind === "daily") return `每天 ${data.time} · ${data.timeZone}`;
  if (data.kind === "weekly") return `每周 ${String(data.weekdays)} · ${data.time} · ${data.timeZone}`;
  if (data.kind === "cron") return `${data.expression} · ${data.timeZone}`;
  return String(data.scheduledAt ?? "单次任务");
}
function ScheduleForm({ item, busy, onCancel, onSave }: { item: DshItem; busy: boolean; onCancel: () => void; onSave: (value: Record<string, unknown>) => void }) {
  const [title, setTitle] = useState(item.title), [prompt, setPrompt] = useState(item.detail);
  const [timing, setTiming] = useState("keep"), [value, setValue] = useState("");
  const [zone, setZone] = useState(String(item.data?.timeZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone));
  const save = () => {
    const change = timing === "every" ? { kind: "every", every_seconds: Number(value) }
      : timing === "daily" ? { kind: "daily", daily: { time: value, time_zone: zone } }
      : timing === "cron" ? { kind: "cron", cron: { expression: value, time_zone: zone } }
      : timing === "at" ? { kind: "at", at: value } : undefined;
    onSave({ title, prompt, ...(change ? { change } : {}) });
  };
  return <form className="dsh-schedule-form" onSubmit={event => { event.preventDefault(); save(); }}>
    <label>任务名称<input required maxLength={120} value={title} onChange={event => setTitle(event.target.value)} /></label>
    <label>任务内容<textarea required value={prompt} onChange={event => setPrompt(event.target.value)} maxLength={24000} /></label>
    <label>执行时间<select value={timing} onChange={event => { setTiming(event.target.value); setValue(""); }}><option value="keep">保持当前时间</option><option value="at">指定时间</option><option value="every">固定间隔</option><option value="daily">每天</option><option value="cron">Cron 表达式</option></select></label>
    {timing !== "keep" && <input aria-label="新的执行时间" required type={timing === "every" ? "number" : timing === "daily" ? "time" : "text"} min={1} placeholder={timing === "at" ? "2026-10-23T09:00:00+08:00" : timing === "every" ? "间隔秒数" : "0 9 * * 1-5"} value={value} onChange={event => setValue(event.target.value)} />}
    {["daily", "cron"].includes(timing) && <label>时区<input required value={zone} onChange={event => setZone(event.target.value)} /></label>}
    <div className="dsh-management-actions"><button type="button" disabled={busy} onClick={onCancel}>取消</button><button className="dsh-primary" disabled={busy || !title.trim() || !prompt.trim()}>保存任务</button></div>
  </form>;
}
