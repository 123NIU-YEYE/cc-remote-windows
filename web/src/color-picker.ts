export interface HSV { h: number; s: number; v: number }
const clamp = (n: number, max: number) => Math.min(max, Math.max(0, n));

export function hexToHsv(hex: string): HSV {
  const [r, g, b] = [1, 3, 5].map(start => parseInt(hex.slice(start, start + 2), 16) / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), delta = max - min;
  const sector = !delta ? 0 : max === r ? (g - b) / delta : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  return { h: (sector * 60 + 360) % 360, s: max ? delta / max * 100 : 0, v: max * 100 };
}

export function hsvToHex({ h, s, v }: HSV): string {
  const hue = ((h % 360) + 360) % 360 / 60;
  const value = clamp(v, 100) / 100, chroma = value * clamp(s, 100) / 100;
  const x = chroma * (1 - Math.abs(hue % 2 - 1)), base = value - chroma;
  const channels = hue < 1 ? [chroma, x, 0] : hue < 2 ? [x, chroma, 0] : hue < 3 ? [0, chroma, x]
    : hue < 4 ? [0, x, chroma] : hue < 5 ? [x, 0, chroma] : [chroma, 0, x];
  return `#${channels.map(c => Math.round((c + base) * 255).toString(16).padStart(2, "0")).join("").toUpperCase()}`;
}
