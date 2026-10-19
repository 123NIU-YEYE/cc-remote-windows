import { APPEARANCE_FONTS, type AppearanceFont as Font } from "../appearance";
import { AppearanceSelect } from "./AppearanceSelect";

export function AppearanceFont({ value, onChange }: { value: Font; onChange: (font: Font) => void }) {
  return <AppearanceSelect label="字体" value={value} displayValue={APPEARANCE_FONTS[value].label}
    onChange={font => onChange(font as Font)} options={Object.entries(APPEARANCE_FONTS).map(([font, item]) => ({
      value: font, label: item.label, sample: <span className="appearance-font-sample" aria-hidden="true"
        style={{ fontFamily: item.stack ?? 'system-ui,-apple-system,"PingFang SC",sans-serif' }}>Aa</span>,
    }))} />;
}
