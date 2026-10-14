import type { Engine } from "../protocol";

export type EngineSurface = Engine | "dsh";

interface Props {
  selected: EngineSurface;
  onSelect: (surface: EngineSurface) => void;
}

export function EngineSwitcher({ selected, onSelect }: Props) {
  return (
    <div className="engine-switcher" role="group" aria-label="切换助手"
      data-lock-horizontal-swipe>
      {(["codex", "claude", "dsh"] as const).map((surface) => (
        <button key={surface} type="button" className="engine-choice"
          aria-pressed={selected === surface}
          title={surface === "dsh" ? "DeepSeek Harness 桌面版" : undefined}
          onClick={() => onSelect(surface)}>
          {surface === "codex" ? "Codex" : surface === "claude" ? "Claude" : "DSH"}
        </button>
      ))}
    </div>
  );
}
