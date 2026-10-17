(function installCcRemoteAndroidShell(expectedOrigin) {
  "use strict";
  // This adapter is packaged in the APK and runs only in its configured main page.
  // The external browser and preview iframes do not receive it.
  if (window.top !== window || location.origin !== expectedOrigin) return;
  if (document.getElementById("cc-remote-android-style")) return;
  const style = document.createElement("style");
  style.id = "cc-remote-android-style";
  style.textContent = `
    @media (max-width:979px) {
      /* Keep the existing transform/veil gesture. Whole-page animated blur adds
         a large offscreen surface in compatibility WebViews, including at rest. */
      .shell > .pane { filter:none !important; will-change:transform; }
    }
    .header-menu-item[data-android-client-settings] {
      text-decoration:none; cursor:pointer;
    }
    .header-menu-item[data-android-client-settings] > svg { flex:none; }
    #cc-remote-android-settings-fallback {
      position:fixed; right:12px; top:12px; z-index:100;
      padding:10px 14px; border:1px solid var(--border,#d9dfeb); border-radius:20px;
      background:var(--surface,#fff); color:var(--dim,#526079);
      font:14px system-ui,sans-serif; text-decoration:none;
    }
    body:has(.header-menu-trigger) > #cc-remote-android-settings-fallback { display:none; }
  `;
  document.head.append(style);

  const settingsUrl = "cc-remote-app://settings";
  const chinese = /^zh\b/i.test(navigator.language) || /^zh\b/i.test(document.documentElement.lang);
  // Login or an incompatible server page has no HeaderMenu yet. Keep settings reachable
  // without a permanent second app bar. CSS hides this link as soon as the real header mounts.
  const fallback = document.createElement("a");
  fallback.id = "cc-remote-android-settings-fallback";
  fallback.href = settingsUrl;
  fallback.textContent = chinese ? "客户端设置" : "Client settings";
  document.body.append(fallback);
  function settingsLink() {
    const link = document.createElement("a");
    link.href = settingsUrl;
    link.className = "header-menu-item";
    link.dataset.androidClientSettings = "";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "18");
    svg.setAttribute("height", "18");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.7");
    svg.setAttribute("aria-hidden", "true");
    const shape = document.createElementNS(svg.namespaceURI, "path");
    shape.setAttribute("d", "M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm3 14h4");
    svg.append(shape);
    const text = document.createElement("span");
    const title = document.createElement("b");
    title.textContent = chinese ? "客户端设置" : "Client settings";
    const subtitle = document.createElement("small");
    subtitle.textContent = chinese ? "服务地址、刷新与应用信息" : "Server, reload and app information";
    text.append(title, subtitle);
    link.append(svg, text);
    return link;
  }

  const menus = new Map();
  function updateMenu(card) {
    const items = card.querySelector(".header-menu-items");
    if (!items || items.querySelector("[data-android-client-settings]")) return;
    items.insertBefore(settingsLink(), items.querySelector(".danger"));
  }
  function attachMenu(scrim) {
    if (!(scrim instanceof Element) || !scrim.matches(".header-menu-scrim")) return;
    const card = scrim.querySelector(".header-menu-card");
    if (!card || menus.has(scrim)) return;
    updateMenu(card);
    // Only the small settings card needs subtree observation (notification back).
    // Never observe the chat/streaming subtree or schedule animation-frame polling.
    const observer = new MutationObserver(() => updateMenu(card));
    observer.observe(card, { childList: true, subtree: true });
    menus.set(scrim, observer);
  }
  document.body.querySelectorAll(":scope > .header-menu-scrim").forEach(attachMenu);
  const portals = new MutationObserver(records => {
    for (const record of records) {
      record.removedNodes.forEach(node => {
        menus.get(node)?.disconnect();
        menus.delete(node);
      });
      record.addedNodes.forEach(attachMenu);
    }
  });
  // HeaderMenu portals are direct body children; message deltas do not trigger this.
  portals.observe(document.body, { childList: true });
})
