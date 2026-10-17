# cc-remote HarmonyOS client (preview)

This is a HarmonyOS **HAP** application using ArkTS/ArkUI and the system's ArkWeb
component. It opens directly from the launcher and loads the existing cc-remote
HTTPS UI. It does not use Android, 卓易通, or an external browser for the chat.
The conversation UI stays web-based; it is not a rewrite of the UI in ArkUI.

## Current scope

- HTTPS server setup, followed by the normal cc-remote cookie login.
- No additional address bar above chat. **⋮ → 客户端设置** offers reload, server
  selection and app information; a small settings entry remains before login.
- Separate in-app web browsing with **返回聊天**, retaining the mounted chat and
  its draft. HTTPS links and user-triggered new windows are supported.
- System file selection through ArkWeb's default file-upload handling.
- System-bar and keyboard avoidance; the page retains its existing sidebar
  gestures, rounding and fading. No additional native horizontal recognizer or
  Android-specific blur override is installed.
- App background/foreground never reloads chat or submits a model message.

Native push, camera/microphone capture, download management, non-HTTPS external
app links and browser-control MCP are outside this first preview. Persistent
login remains subject to the Relay cookie's normal expiry. No cookie, model
credential, session payload or device-specific server address is stored in this
repository. Only the user-entered server origin is saved in native preferences.

## Prepare and build on macOS

Install the official **Mac ARM** edition of
[DevEco Studio](https://developer.huawei.com/consumer/cn/download/deveco-studio).
The project uses the Stage model, compatible API 14+, and APIs available by
API 20. Compilation uses DevEco's bundled HarmonyOS SDK by default; the initial
build was checked with DevEco Studio 26.0.0 and its API 26 SDK.

From the repository root:

```bash
python3 harmony/tools/project.py init
python3 harmony/tools/project.py check
python3 harmony/tools/project.py build
```

For a nonstandard installation, add `--studio /path/to/DevEco-Studio.app`.
Open **the `harmony/` directory** in DevEco Studio. The `init` command creates the
two local `build-profile.json5` files from tracked templates. These local files,
`local.properties`, IDE files and signing certificates are gitignored. Existing
local profiles are never overwritten; do not commit automatic-signing fields or
certificate passwords to the templates. No server deployment is needed.

## Install on your own phone

This app can be tested without an application-market listing, but a real device
still requires a valid debug signature. In DevEco Studio:

1. Sign in to your Huawei developer account and complete its required account
   verification if prompted.
2. Enable developer options and USB debugging on the phone, connect it to the
   Mac, and accept the device's debugging prompt.
3. Open **File → Project Structure → Signing Configs**. Use automatic debug
   signing; association with a registered market application is not required
   for this basic private trial. Ensure the `default` product uses the generated
   signing configuration. If a command-line build produces only an unsigned HAP,
   check `app.products[].signingConfig` in the local `build-profile.json5`.
4. Select the connected phone and Run. Enter your HTTPS Relay origin and log in.

The debug profile is bound to allowed devices and has an expiry. Check that
expiry in the IDE; renew/re-sign as required. An unsigned HAP is a build artifact,
not a file that can be tapped to install on a normal retail phone. Keep the same
bundle name and signing identity for upgrades, incrementing `versionCode`.

## Updates and daily use

The installed app runs independently of DevEco Studio and the USB connection.
After installation, exit the IDE and disconnect the phone. If the local HDC
server remains running, stop it with
`<DevEco-Studio.app>/Contents/sdk/default/openharmony/toolchains/hdc kill`.
Keep the SDK and private signing configuration for the next native update.

Most chat features come from the hosted Web UI. After a normal cc-remote server
update, use **⋮ → 客户端设置 → 刷新聊天页面** to load the updated UI; a new HAP is
not needed. Changes to the ArkUI/ArkWeb shell or its packaged adapter require a
new signed HAP and an in-place installation. Preserve the bundle and signing
identity, increment `versionCode`, and install over the existing app instead of
uninstalling it so its local preferences and browser data can be retained.
Cookie expiry still follows the Relay's login policy. This preview does not
include an in-app native updater, and its debug profile must be renewed when it
expires.

Official references:

- [Automatic signing](https://developer.huawei.com/consumer/cn/doc/doccenter-deveco-studio/ide-signing-auto)
- [Signing requirements](https://developer.huawei.com/consumer/cn/develop-novice-guide/)
- [ArkWeb file upload](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/web-file-upload)
- [ArkWeb new windows](https://developer.huawei.com/consumer/cn/doc/doccenter-capabilities/web-open-in-new-window)

## Verification

With Node 24 selected and the existing `web/` development dependencies installed:

```bash
node --test harmony/tests/url-policy.test.mjs
node web/node_modules/@playwright/test/cli.js test -c harmony/playwright.config.mjs
```

The policy tests execute the production TypeScript with a WHATWG URL substitute
for ArkTS URL. Browser checks use the real Web settings menu and sidebar fixture.
Neither substitutes for ArkTS compilation or a real-device ArkWeb test.

The initial preview was compiled and signed with DevEco Studio 26.0.0, installed
on a physical HarmonyOS 7 phone, and checked at first launch. The operator also
reported smooth sidebar gestures. This is a usability check, not a measured
frame-rate result or completion of every acceptance item below. The URL-policy
suite (5 cases) and browser adapter suite (4 cases) passed.

Before calling a package accepted, compile it with DevEco, install a signed build
on the target phone, and check:

- Cold open, login, app background/foreground, login persistence and reconnect.
- Open/close the sidebar slowly and with a flick, both idle and while streaming;
  compare the same session with Huawei Browser. Do not claim a measured frame
  rate without a device capture.
- Keyboard resize, attachment selection/cancel/reselect, and file previews.
- Open an HTTPS link and a target-blank preview, navigate back and return to the
  unchanged draft; cancel a popup while it is attaching.
- Client settings, cancelling server change, offline recovery and server change.
- TLS errors remain rejected, and external/iframe pages cannot open client
  settings or access privileged native methods.

There are no exposed JavaScript native objects, credentials in URLs, TLS bypass,
frame-origin relaxations, or engine/daemon changes. The only app URL command opens
the settings dialog after an exact-origin, top-frame and user-gesture check.
