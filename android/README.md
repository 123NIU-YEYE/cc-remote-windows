# Android client (preview)

A small Android WebView client for cc-remote. It loads a user-selected HTTPS
Relay directly, so login cookies, WebSocket origins, and the existing web UI keep
their normal boundaries. It does not bundle Claude/Codex, change a daemon, or
access model credentials. There is no dependency on Google Play services.

For HarmonyOS devices, use the [native ArkWeb client](../harmony/README.md).
This APK can also be tried through 卓易通, but compatibility and gesture
performance depend on that environment. It is **not** a native HarmonyOS HAP
or a claim that HarmonyOS supports APKs without a compatibility environment.

## Try it

1. Install the signed APK in Android, or import it into 卓易通 using that
   environment's APK installation entry.
2. Enter your cc-remote **HTTPS server origin**, for example
   `https://remote.example.com`. Do not include login credentials or URL paths.
3. Log in using the normal cc-remote login page. The cookie stays in the app's
   private WebView profile; reopening the app does not deliberately log you out.
4. Open **⋮ → Client settings** in the existing chat header to reload, change
   server, or check the WebView version. Before login, a small **Client settings**
   link provides the same entry. The chat has no extra native address bar.

Web links open in an in-app browsing view. Back returns through that view's
history; its menu also offers **Return to chat** directly. The chat view stays
mounted, preserving its current session and draft. At the root, Android Back
puts the app in the background. Returning to the app does not automatically
reload or resubmit a message. Server-side tasks do not depend on the app staying
in the foreground.

The APK applies a small adapter only to its configured chat page. On mobile
layouts it keeps the sidebar's movement and gradual fade, but removes the
whole-chat blur to reduce rendering work in compatibility WebViews. On Android
API 35 and later it also requests a higher frame-rate category during touch and
the following animation; the OS and compatibility environment decide whether
to honor that request. These changes do not affect ordinary browsers or PWA
clients, and do not guarantee a specific frame rate in 卓易通.

The system file picker supports selecting existing images and documents, with
multiple selection when the web input requests it. No broad storage permission
is requested. This preview does **not** implement native push notifications,
camera/microphone access, saving downloads (including blob downloads), or MCP
browser control. An Android WebView is not a full Chrome installation; desktop
extensions and Chrome's browser APIs are not included.

Use a recent Android System WebView compatible with the current cc-remote web
bundle. Android 8.0 (API 26) is the minimum installation level, not a guarantee
that an old bundled WebView can render the current web UI. When using 卓易通,
its WebView/file-picker/keyboard integrations are part of the device acceptance.

## Build

Requirements: JDK 17, Android SDK platform 36, Build Tools 35.0.0. Gradle 8.13 and
Android Gradle Plugin 8.13.2 are pinned. This project builds separately from Web
and does not modify or deploy the server.

```bash
cd android
# Set JAVA_HOME and ANDROID_HOME for your local tools, or use local.properties
# containing sdk.dir=/absolute/path/to/android-sdk (gitignored).
./gradlew testDebugUnitTest lintDebug assembleDebug assembleRelease
```

Outputs are under `app/build/outputs/`. The debug APK is development-signed.
`app-release-unsigned.apk` requires signing before it can be installed. Do not
distribute a debug APK as the update path for users of a release-signed APK.

To create a private signing key, keep the keystore and password file outside the
repository, restrict access, and back them up securely. For example, with the
password already stored in `$ANDROID_KEY_PASSWORD_FILE`:

```bash
keytool -genkeypair -keystore "$ANDROID_RELEASE_KEYSTORE" \
  -storepass:file "$ANDROID_KEY_PASSWORD_FILE" \
  -keypass:file "$ANDROID_KEY_PASSWORD_FILE" \
  -alias cc-remote -keyalg RSA -keysize 3072 -validity 10000 \
  -dname 'CN=cc-remote Android'

"$ANDROID_HOME/build-tools/35.0.0/zipalign" -f -p 4 \
  app/build/outputs/apk/release/app-release-unsigned.apk /tmp/cc-remote-aligned.apk
"$ANDROID_HOME/build-tools/35.0.0/apksigner" sign \
  --ks "$ANDROID_RELEASE_KEYSTORE" --ks-key-alias cc-remote \
  --ks-pass "file:$ANDROID_KEY_PASSWORD_FILE" \
  --out /tmp/cc-remote-android.apk /tmp/cc-remote-aligned.apk
"$ANDROID_HOME/build-tools/35.0.0/apksigner" verify --verbose --print-certs \
  /tmp/cc-remote-android.apk
```

Keep the same signing key and application ID for future updates, and increment
`versionCode`. The Android shell version is independent of the Relay/Web
release. Updating the Relay updates the hosted web UI; changing native behavior
requires a new APK. There is no APK auto-updater in this preview.

## Verification

JVM tests cover origin validation and navigation trust boundaries. Android Lint
and both APK builds must pass. Offline WebView instrumentation tests cover
external-link return, draft preservation during rotation, and cancelling and
reopening the attachment picker. Run them with an Android emulator/device
connected: `./gradlew connectedDebugAndroidTest`. They use fixture HTML and do
not connect to a real cc-remote session or call a model.

The packaged page adapter also has browser regression checks against the real
Web sidebar and settings menu. With the Web dependencies installed and Node 24
selected, run from the repository root:

```bash
node web/node_modules/@playwright/test/cli.js test -c android/playwright.config.mjs
```

These checks verify gesture positioning, fading, menu integration and origin
isolation; they do not measure device frame rate or replace WebView acceptance.

Before wider distribution, test the signed APK
on the intended Android/卓易通 device:

- Configure the server, log in, close/reopen, and confirm login persistence.
- Open a session, type with the keyboard, and send a short test message once.
- Switch away and return while a task runs; confirm no duplicate submission.
- Select an image and a text attachment, including cancellation and re-selection.
- Open a web link, navigate, and return to the unchanged chat and draft.
- Rotate the screen, open/close the keyboard, and test sidebar gestures.
- Lose and restore the network; check the existing web reconnect behavior.

No TLS-error bypass, arbitrary `intent:` handling, JavaScript-exposed native
object, or cross-origin WebSocket exceptions are introduced. The settings entry
uses one exact app URL, accepted only for a user-initiated top-level navigation
from the configured chat origin, to open the native settings dialog. External HTTP, mail and phone
links require confirmation before opening another app. Native downloads are
explicitly unavailable rather than forwarding authenticated URLs/cookies to a
separate downloader.
