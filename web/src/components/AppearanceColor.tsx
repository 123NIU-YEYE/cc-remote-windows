import { useId, useRef, useState, type PointerEvent } from "react";
import { normalizeHex } from "../appearance";
import { hexToHsv, hsvToHex, type HSV } from "../color-picker";
import { Icon } from "../icons";
import { AppearancePopover } from "./AppearancePopover";

function HexInput({ value, label, onChange }: { value: string; label: string; onChange: (color: string) => void }) {
  const [edit, setEdit] = useState({ value, text: value });
  const text = edit.value === value ? edit.text : value;
  const apply = (next: string) => { setEdit({ value: next, text: next }); onChange(next); };
  return <input type="text" aria-label={label} aria-invalid={!normalizeHex(text)} maxLength={7} spellCheck={false}
    autoComplete="off" autoCapitalize="characters" value={text} placeholder="#RRGGBB"
    title="输入 #RRGGBB 或 #RGB 色值" onChange={event => {
      const next = event.target.value;
      const normalized = /^#[\da-f]{6}$/i.test(next) ? normalizeHex(next) : undefined;
      if (normalized) apply(normalized);
      else setEdit({ value, text: next });
    }} onBlur={() => {
      const normalized = normalizeHex(text);
      if (normalized) apply(normalized);
      else setEdit({ value, text: value });
    }} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }} />;
}

const SWATCHES = ["#6F69D2", "#4C83C3", "#C47749", "#518C69", "#BE718E", "#252334", "#FAFAFC"];

function ColorPalette({ label, value, onChange, onReset, onClose }: {
  label: string; value: string; onChange: (color: string) => void; onReset: () => void; onClose: () => void;
}) {
  // Keep hue and saturation when choosing white/black, where HEX cannot encode them.
  const [selection, setSelection] = useState(() => ({ ...hexToHsv(value), hex: value }));
  const color = selection.hex === value ? selection : hexToHsv(value);
  const pointer = useRef<number | null>(null);
  const change = (next: HSV) => {
    const hex = hsvToHex(next);
    setSelection({ ...next, hex });
    onChange(hex);
  };
  const pick = (hex: string) => { setSelection({ ...hexToHsv(hex), hex }); onChange(hex); };
  const drag = (event: PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    if (!box.width || !box.height) return;
    change({ ...color, s: Math.min(100, Math.max(0, (event.clientX - box.left) / box.width * 100)),
      v: Math.min(100, Math.max(0, (1 - (event.clientY - box.top) / box.height) * 100)) });
  };
  return <>
    <header className="appearance-palette-head"><span>{label}</span>
      <button type="button" aria-label={`关闭${label}取色器`} onClick={onClose}><Icon name="close" size={16} /></button>
    </header>
    <div className="appearance-sv" role="group" aria-label="饱和度与亮度"
      style={{ backgroundColor: `hsl(${color.h} 100% 50%)` }}
      onPointerDown={event => {
        if (!event.isPrimary || event.button !== 0) return;
        event.preventDefault();
        pointer.current = event.pointerId;
        event.currentTarget.setPointerCapture(event.pointerId);
        event.currentTarget.querySelector<HTMLInputElement>("input")?.focus({ preventScroll: true });
        drag(event);
      }} onPointerMove={event => { if (pointer.current === event.pointerId) drag(event); }}
      onPointerUp={event => {
        if (pointer.current !== event.pointerId) return;
        drag(event); pointer.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
      }} onPointerCancel={() => { pointer.current = null; }} onLostPointerCapture={() => { pointer.current = null; }}>
      <span className="appearance-sv-thumb" aria-hidden="true"
        style={{ left: `${color.s}%`, top: `${100 - color.v}%`, backgroundColor: value }} />
      <input className="appearance-sv-range" type="range" aria-label="饱和度" aria-valuetext={`${Math.round(color.s)}%`}
        min={0} max={100} value={color.s} onChange={event => change({ ...color, s: Number(event.target.value) })} />
      <input className="appearance-sv-range" type="range" aria-label="亮度" aria-valuetext={`${Math.round(color.v)}%`}
        min={0} max={100} value={color.v} onChange={event => change({ ...color, v: Number(event.target.value) })} />
    </div>
    <input className="appearance-hue" type="range" aria-label="色相" aria-valuetext={`${Math.round(color.h)}°`}
      style={{ color: `hsl(${color.h} 100% 50%)` }}
      min={0} max={360} value={color.h} onChange={event => change({ ...color, h: Number(event.target.value) })} />
    <div className="appearance-palette-hex">
      <span className="appearance-swatch" style={{ backgroundColor: value }} aria-hidden="true" />
      <span>HEX</span><HexInput label={`${label}取色器 HEX`} value={value} onChange={pick} />
    </div>
    <div className="appearance-swatches" role="group" aria-label="常用颜色">
      <span>常用颜色</span><div>{SWATCHES.map(hex => <button type="button" key={hex}
        aria-label={`使用颜色 ${hex}`} aria-pressed={hex === value} style={{ backgroundColor: hex }}
        onClick={() => pick(hex)} />)}</div>
    </div>
    <footer className="appearance-palette-footer"><span>实时预览</span>
      <button type="button" onClick={onReset}>恢复默认</button>
    </footer>
  </>;
}

export function AppearanceColor({ label, value, onChange, onReset }: {
  label: string; value: string; onChange: (color: string) => void; onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const close = (restore: boolean) => { setOpen(false); if (restore) trigger.current?.focus({ preventScroll: true }); };
  return <div className="appearance-row">
    <span className="appearance-label">{label}</span>
    <div className="appearance-color">
      <button type="button" className="appearance-swatch" ref={trigger} aria-label={`选择${label}`}
        aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined}
        style={{ backgroundColor: value }} onClick={() => setOpen(!open)} />
      <HexInput label={`${label} HEX`} value={value} onChange={onChange} />
    </div>
    {open && <AppearancePopover anchor={trigger} id={id} label={`${label}取色器`} onClose={close}>
      <ColorPalette label={label} value={value} onChange={onChange} onReset={onReset} onClose={() => close(true)} />
    </AppearancePopover>}
  </div>;
}
