import { useId, useRef, useState, type ReactNode } from "react";
import { Icon } from "../icons";
import { AppearancePopover } from "./AppearancePopover";

export function AppearanceSelect({ label, value, displayValue, options, width = 240, onChange }: {
  label: string;
  value?: string;
  displayValue: string;
  options: { value: string; label: string; sample: ReactNode }[];
  width?: number;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const close = (restore: boolean) => { setOpen(false); if (restore) trigger.current?.focus({ preventScroll: true }); };
  return <div className="appearance-row">
    <span className="appearance-label">{label}</span>
    <button type="button" className="appearance-select-trigger" ref={trigger} role="combobox" aria-label={label}
      aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}
      onKeyDown={event => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setOpen(true); }
      }}>
      <span>{displayValue}</span><Icon name="chev" size={14} />
    </button>
    {open && <AppearancePopover anchor={trigger} id={id} label={`${label}选项`} role="listbox" width={width} onClose={close}>
      {options.map((option, index) => <button type="button" key={option.value}
        className="appearance-select-option" role="option" aria-label={option.label} aria-selected={option.value === value}
        tabIndex={option.value === value || (value === undefined && index === 0) ? 0 : -1}
        onClick={() => { onChange(option.value); close(true); }}
        onKeyDown={event => {
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
          event.preventDefault(); event.stopPropagation();
          const nodes = [...event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>('[role="option"]')];
          const current = nodes.indexOf(event.currentTarget);
          nodes[event.key === "Home" ? 0 : event.key === "End" ? nodes.length - 1
            : (current + (event.key === "ArrowUp" ? -1 : 1) + nodes.length) % nodes.length].focus();
        }}>
        <span>{option.label}</span>{option.sample}
      </button>)}
    </AppearancePopover>}
  </div>;
}
