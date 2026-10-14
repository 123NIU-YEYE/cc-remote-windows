/** Runs inside the preview realm. Keep this function self-contained: the HTML
 * runner embeds its source without granting access to the parent document. */
export function installPreviewEscape() {
  if (window === parent) return;
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.isComposing && event.keyCode !== 229) {
      // Give the preview's own handlers (including later window listeners)
      // their normal chance to consume Escape before asking its host to dock.
      setTimeout(() => {
        if (!event.defaultPrevented) parent.postMessage({ type: "cc-remote-preview-escape" }, "*");
      }, 0);
    }
  });
}
