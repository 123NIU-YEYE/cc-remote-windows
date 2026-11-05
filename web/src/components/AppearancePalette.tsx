import { appearanceColors, appearancePaletteLabel, isCustomPalette, type AppearanceLook } from "../appearance";
import { THEME_PALETTES, type PaletteId, type ThemeEngine, type ThemeMode } from "../themes";
import { AppearanceSelect } from "./AppearanceSelect";

export function AppearancePalette({ engine, mode, look, onChange }: {
  engine: ThemeEngine; mode: ThemeMode; look: AppearanceLook; onChange: (preset: PaletteId) => void;
}) {
  return <AppearanceSelect label="配色方案" value={isCustomPalette(engine, mode, look) ? undefined : look.preset}
    displayValue={appearancePaletteLabel(engine, mode, look)} width={280}
    onChange={preset => onChange(preset as PaletteId)}
    options={THEME_PALETTES.filter(palette => palette.mode === mode).map(palette => {
      const colors = appearanceColors(engine, mode, { preset: palette.id });
      return { value: palette.id, label: palette.name, sample: <span className="appearance-palette-samples" aria-hidden="true">
        {[colors.background, colors.accent, colors.foreground].map((color, index) =>
          <i key={index} style={{ backgroundColor: color }} />)}
      </span> };
    })} />;
}
