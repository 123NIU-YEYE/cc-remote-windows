import { useRef, useState, type FormEvent, type RefObject } from "react";
import { CenteredSheet } from "./CenteredSheet";
import "./PasswordChangeSheet.css";

export default function PasswordChangeSheet({ onClose, onChanged, trigger }: {
  onClose: () => void;
  onChanged: () => void;
  trigger: RefObject<HTMLElement | null>;
}) {
  const submitting = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting.current) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const current = String(data.get("current_password") ?? "");
    const next = String(data.get("new_password") ?? "");
    if (next !== data.get("confirm_password")) {
      setError("两次输入的新密码不一致");
      return;
    }
    const length = Array.from(next).length;
    if (next === current || !next.trim() || length < 12 || length > 256) {
      setError("请使用不同于当前密码、长度为 12 至 256 个字符的新密码");
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/change-password", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      const result = await response.json().catch(() => null);
      if (response.ok && result?.ok === true) {
        form.reset();
        onChanged();
      } else if (result?.error === "invalid_current_password") {
        setError("当前密码错误");
      } else if (result?.error === "invalid_new_password") {
        setError("新密码须为 12 至 256 个字符，并且不同于当前密码");
      } else if (response.status === 429) {
        setError("尝试太频繁，请稍后再试");
      } else if (response.status === 401) {
        setError("登录已失效，请重新登录后再修改密码");
      } else {
        setError("修改失败，请检查中继是否支持网页改密");
      }
    } catch {
      setError("连接中断，修改结果待确认。请先用新密码尝试登录。");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  return <CenteredSheet open label="修改密码" onClose={() => { if (!submitting.current) onClose(); }}
    maxWidth={440} minimumHeight={280} returnFocusRef={trigger}>
    <form className="sheet-scroll password-change-form" method="post" action="/api/change-password"
      onSubmit={(event) => void submit(event)} aria-busy={busy}>
      <p>修改后，本账号的已登录浏览器会退出。请用新密码重新登录。</p>
      <label>当前密码<span className="login-field">
        <input type="password" name="current_password" autoComplete="current-password" required disabled={busy} />
      </span></label>
      <label>新密码<span className="login-field">
        <input type="password" name="new_password" autoComplete="new-password" required disabled={busy} />
      </span></label>
      <label>确认新密码<span className="login-field">
        <input type="password" name="confirm_password" autoComplete="new-password" required disabled={busy} />
      </span></label>
      {error && <p className="login-err" role="alert">{error}</p>}
      <button type="submit" className="login-btn" disabled={busy}>{busy ? "正在修改…" : "保存新密码"}</button>
    </form>
  </CenteredSheet>;
}
