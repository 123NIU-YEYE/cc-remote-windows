// Only recolor a neutral silhouette. Colored artwork, opaque backgrounds, and
// black/white internal details must keep their original pixels.
export function isNeutralSilhouette(pixels: Uint8ClampedArray): boolean {
  const count = pixels.length / 4;
  if (!count) return false;
  let transparent = 0, visible = 0, darkest = 255, lightest = 0;
  for (let i = 0; i < pixels.length; i += 4) {
    const alpha = pixels[i + 3];
    if (alpha < 16) transparent++;
    if (alpha < 64) continue;
    visible++;
    const low = Math.min(pixels[i], pixels[i + 1], pixels[i + 2]);
    const high = Math.max(pixels[i], pixels[i + 1], pixels[i + 2]);
    if (high - low > 24) return false;
    darkest = Math.min(darkest, low);
    lightest = Math.max(lightest, high);
  }
  return transparent >= count * .1 && visible >= count * .05 && lightest - darkest <= 32;
}

const silhouettes = new Map<string, boolean>();

export function iconUsesThemeColor(image: HTMLImageElement): boolean {
  const src = image.currentSrc || image.src;
  const cached = silhouettes.get(src);
  if (cached !== undefined) return cached;
  // Relay icons are bounded static PNGs. Never read remote pixels or allocate a
  // canvas from untrusted image dimensions; failure leaves the original intact.
  if (!src.startsWith("data:image/png;base64,") || src.length > 90000
    || !image.naturalWidth || !image.naturalHeight
    || image.naturalWidth > 128 || image.naturalHeight > 128) return false;
  let monochrome = false;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (context) {
      context.drawImage(image, 0, 0);
      monochrome = isNeutralSilhouette(context.getImageData(0, 0, canvas.width, canvas.height).data);
    }
  } catch { /* Keep the original icon if canvas access is unavailable. */ }
  silhouettes.set(src, monochrome);
  while (silhouettes.size > 64) silhouettes.delete(silhouettes.keys().next().value!);
  return monochrome;
}
