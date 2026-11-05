(function installCcRemoteHarmonyShell(expectedOrigin) {
  "use strict";
  // This adapter is packaged in the HAP and runs only in its configured main page.
  // The external browser and preview iframes do not receive it.
  if (window.top !== window || location.origin !== expectedOrigin) return;
  if (document.getElementById("cc-remote-harmony-style")) return;
  const style = document.createElement("style");
  style.id = "cc-remote-harmony-style";
  style.textContent = `
    #cc-remote-harmony-settings-fallback {
      position:fixed; right:12px; top:12px; z-index:100;
      padding:10px 14px; border:1px solid var(--border,#d9dfeb); border-radius:20px;
      background:var(--surface,#fff); color:var(--dim,#526079);
      font:14px system-ui,sans-serif; text-decoration:none;
    }
    body:has(.header-menu-trigger) > #cc-remote-harmony-settings-fallback { display:none; }
  `;
  document.head.append(style);

  const settingsUrl = "cc-remote-app://settings";
  const chinese = /^zh\b/i.test(navigator.language) || /^zh\b/i.test(document.documentElement.lang);
  // Login or an incompatible server page has no HeaderMenu yet. Keep settings reachable
  // without a permanent second app bar. CSS hides this link as soon as the real header mounts.
  const fallback = document.createElement("a");
  fallback.id = "cc-remote-harmony-settings-fallback";
  fallback.href = settingsUrl;
  fallback.textContent = chinese ? "客户端设置" : "Client settings";
  document.body.append(fallback);
})
