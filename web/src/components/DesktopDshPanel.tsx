import { useState } from "react";

// Mount lazily and retain the iframe while hidden, so switching assistants
// doesn't reload DSH, discard its editor draft, or reconnect its live stream.
export function DesktopDshPanel({ visible }: { visible: boolean }) {
  const [loaded, setLoaded] = useState(false);
  return (
    <section className="desktop-dsh-panel" hidden={!visible} aria-label="DeepSeek Harness"
      data-lock-horizontal-swipe>
      {!loaded && <p className="dsh-loading" role="status">正在连接 DeepSeek 桌面版…</p>}
      <iframe className="dsh-frame" src="/dsh/" title="DeepSeek Harness 桌面版"
        onLoad={() => setLoaded(true)} />
    </section>
  );
}
