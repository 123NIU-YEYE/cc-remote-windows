package io.github.ccremote.android;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Message;
import android.text.InputType;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

/** A control-link client. Engines, model authentication and work remain on the user's machine. */
public final class MainActivity extends Activity {
    private static final int FILE_REQUEST = 41;
    private LinearLayout root;
    private LinearLayout toolbar;
    private FrameLayout pages;
    private TextView address;
    private Button back;
    private ProgressBar progress;
    private Button error;
    private WebView chat;
    private WebView browser;
    private String server;
    private String failedUrl;
    private WebView failedView;
    private ValueCallback<Uri[]> fileCallback;
    private WebView fileOwner;
    private boolean pageFailed;
    private boolean changingServer;
    private String shellScript;
    private final Runnable resetFrameRate = () -> {
        if (Build.VERSION.SDK_INT >= 35) {
            if (chat != null) chat.setRequestedFrameRate(View.REQUESTED_FRAME_RATE_CATEGORY_DEFAULT);
            if (browser != null) browser.setRequestedFrameRate(View.REQUESTED_FRAME_RATE_CATEGORY_DEFAULT);
        }
    };
    private final java.util.Set<WebView> routingViews = new java.util.HashSet<>();

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        try (java.io.InputStream asset = getAssets().open("shell.js")) {
            java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int size;
            while ((size = asset.read(buffer)) != -1) bytes.write(buffer, 0, size);
            shellScript = bytes.toString(java.nio.charset.StandardCharsets.UTF_8.name());
        } catch (java.io.IOException exception) {
            // A damaged/mismatched package must retain a usable native settings entry.
            shellScript = null;
        }
        buildChrome();
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(0, this::goBack);
        }
        server = getPreferences(MODE_PRIVATE).getString("server", null);
        if (server == null) showSetup(); else openChat(state);
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }

    private Button button(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextSize(14);
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setPadding(dp(12), 0, dp(12), 0);
        return button;
    }

    private void buildChrome() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(247, 248, 252));
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars()
                        | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                // Native chrome owns insets. Do not also apply them as WebView safe-area padding.
                return WindowInsets.CONSUMED;
            });
        }
        toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(4), 0, dp(4), 0);
        root.addView(toolbar, new LinearLayout.LayoutParams(-1, dp(48)));
        back = button("‹");
        back.setTextSize(26);
        back.setContentDescription(getString(R.string.browser_back));
        back.setVisibility(View.GONE);
        back.setOnClickListener(v -> goBack());
        toolbar.addView(back, new LinearLayout.LayoutParams(dp(48), -1));
        address = new TextView(this);
        address.setText(R.string.toolbar_brand);
        address.setTextSize(14);
        address.setSingleLine(true);
        address.setEllipsize(android.text.TextUtils.TruncateAt.END);
        address.setPadding(dp(12), 0, dp(8), 0);
        toolbar.addView(address, new LinearLayout.LayoutParams(0, -2, 1));
        Button menu = button("⋮");
        menu.setTextSize(22);
        menu.setContentDescription(getString(R.string.settings));
        menu.setOnClickListener(this::showMenu);
        toolbar.addView(menu, new LinearLayout.LayoutParams(dp(48), -1));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);
        root.addView(progress, new LinearLayout.LayoutParams(-1, dp(2)));
        error = button(getString(R.string.load_error_retry));
        error.setVisibility(View.GONE);
        error.setOnClickListener(v -> {
            if (failedView != null && failedUrl != null) failedView.loadUrl(failedUrl);
            else if (chat == null && server != null) openChat(null);
        });
        root.addView(error, new LinearLayout.LayoutParams(-1, -2));
        pages = new FrameLayout(this);
        root.addView(pages, new LinearLayout.LayoutParams(-1, 0, 1));
    }

    private void showSetup() {
        pages.removeAllViews();
        toolbar.setVisibility(View.VISIBLE);
        address.setText(R.string.toolbar_brand);
        back.setVisibility(View.GONE);
        error.setVisibility(View.GONE);
        progress.setVisibility(View.GONE);
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setGravity(Gravity.CENTER_VERTICAL);
        form.setPadding(dp(28), dp(20), dp(28), dp(20));
        TextView title = new TextView(this);
        title.setText(R.string.app_name);
        title.setTextSize(30);
        form.addView(title);
        TextView subtitle = new TextView(this);
        subtitle.setText(R.string.welcome);
        subtitle.setTextSize(16);
        subtitle.setPadding(0, dp(8), 0, dp(28));
        form.addView(subtitle);
        TextView label = new TextView(this);
        label.setText(R.string.address_label);
        form.addView(label);
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint(R.string.address_hint);
        input.setText(server == null ? "" : server);
        input.setContentDescription(getString(R.string.address_label));
        form.addView(input, new LinearLayout.LayoutParams(-1, dp(56)));
        Button connect = button(getString(R.string.connect));
        connect.setOnClickListener(v -> {
            try {
                String next = UrlPolicy.serverOrigin(input.getText().toString());
                InputMethodManager keyboard = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
                keyboard.hideSoftInputFromWindow(input.getWindowToken(), 0);
                connect.setEnabled(false);
                input.setEnabled(false);
                // Switching origin explicitly clears the shared WebView cookie jar. Never remove
                // account files or model credentials; this is only the browser's control login.
                if (server != null && !server.equals(next)) {
                    changingServer = true;
                    CookieManager.getInstance().removeAllCookies(removed -> {
                        CookieManager.getInstance().flush();
                        changingServer = false;
                        if (!isFinishing() && !isDestroyed()) acceptServer(next);
                    });
                } else acceptServer(next);
            } catch (IllegalArgumentException exception) {
                input.setError(getString(R.string.invalid_address));
            }
        });
        form.addView(connect, new LinearLayout.LayoutParams(-1, dp(56)));
        pages.addView(form, new FrameLayout.LayoutParams(-1, -1));
    }

    private void acceptServer(String next) {
        server = next;
        getPreferences(MODE_PRIVATE).edit().putString("server", server).apply();
        openChat(null);
    }

    private void openChat(Bundle state) {
        pages.removeAllViews();
        failedView = null;
        failedUrl = null;
        error.setVisibility(View.GONE);
        try {
            chat = createWebView(true);
        } catch (RuntimeException exception) {
            error.setText(R.string.missing_webview);
            error.setVisibility(View.VISIBLE);
            return;
        }
        pages.addView(chat, new FrameLayout.LayoutParams(-1, -1));
        if (state == null || state.getBundle("chat") == null || chat.restoreState(state.getBundle("chat")) == null
                || !UrlPolicy.isSameOrigin(server, chat.getUrl())) chat.loadUrl(server + "/");
        refreshToolbar();
    }

    @SuppressLint("SetJavaScriptEnabled")
    private WebView createWebView(boolean isChat) {
        WebView view = new WebView(this);
        view.setOverScrollMode(View.OVER_SCROLL_NEVER);
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setUserAgentString(settings.getUserAgentString() + " cc-remote-android/0.1.0-preview.2");
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(view, false);
        // No JavaScript object exposes native methods. A single, user-clicked settings
        // URL is accepted only from the configured chat origin's top-level document.
        view.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView source, WebResourceRequest request) {
                if (isChat && UrlPolicy.isSettingsRequest(request.getUrl().toString(), server,
                        source.getUrl(), request.isForMainFrame(), request.hasGesture())) {
                    showClientSettings();
                    return true;
                }
                if (!request.isForMainFrame()) return false;
                return route(request.getUrl().toString(), isChat, false);
            }

            @Override public void onPageStarted(WebView source, String url, android.graphics.Bitmap icon) {
                // Also stop programmatic/loadUrl navigation outside the shell's allowed schemes.
                if ((isChat && !UrlPolicy.isSameOrigin(server, url)) || !UrlPolicy.isHttps(url)) {
                    source.stopLoading();
                    route(url, isChat, true);
                    return;
                }
                if (source == visibleWebView()) {
                    pageFailed = false;
                    error.setVisibility(View.GONE);
                }
                refreshToolbar();
            }

            @Override public void onPageFinished(WebView source, String url) {
                if (isChat && shellScript != null && UrlPolicy.isSameOrigin(server, url)) {
                    source.evaluateJavascript(shellScript + "(" + org.json.JSONObject.quote(server) + ");", null);
                }
                CookieManager.getInstance().flush();
                refreshToolbar();
            }

            @Override public void onReceivedError(WebView source, WebResourceRequest request, WebResourceError failure) {
                if (request.isForMainFrame()) showFailure(source, request.getUrl().toString());
            }

            @Override public void onReceivedHttpError(WebView source, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame() && response.getStatusCode() >= 400)
                    showFailure(source, request.getUrl().toString());
            }

            @Override public boolean onRenderProcessGone(WebView source, RenderProcessGoneDetail detail) {
                // The renderer may be reclaimed in a compatibility container. Never resubmit a turn.
                if (source == browser) closeBrowser();
                else {
                    cancelFileChooser(source);
                    pages.removeView(source);
                    source.destroy();
                    chat = null;
                    failedView = null;
                    failedUrl = null;
                    error.setText(R.string.load_error_retry);
                    error.setVisibility(View.VISIBLE);
                    toolbar.setVisibility(View.VISIBLE);
                }
                return true;
            }
        });
        view.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView source, int value) {
                if (source == visibleWebView()) {
                    progress.setProgress(value);
                    // Browser chrome is useful for external pages. Chat owns its own loading UI.
                    progress.setVisibility(browser != null && value < 100 && !pageFailed ? View.VISIBLE : View.GONE);
                }
            }

            @Override public boolean onCreateWindow(WebView source, boolean dialog, boolean userGesture, Message result) {
                if (!userGesture) return false;
                // target=_blank supplies its URL only after WebViewTransport attaches. This blank
                // routing view never executes page script or loads remote content.
                WebView routing = new WebView(MainActivity.this);
                routingViews.add(routing);
                routing.getSettings().setJavaScriptEnabled(false);
                routing.getSettings().setAllowFileAccess(false);
                routing.getSettings().setAllowContentAccess(false);
                routing.setWebViewClient(new WebViewClient() {
                    private boolean routed;
                    @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
                        if (!routed && request.isForMainFrame()) {
                            routed = true;
                            route(request.getUrl().toString(), isChat, true);
                            v.post(() -> destroyRoutingView(v));
                        }
                        return true;
                    }
                });
                ((WebView.WebViewTransport) result.obj).setWebView(routing);
                result.sendToTarget();
                // A popup closed before navigation must not leave a renderer attached indefinitely.
                routing.postDelayed(() -> destroyRoutingView(routing), 5000);
                return true;
            }

            @Override public boolean onShowFileChooser(WebView source, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                fileOwner = source;
                Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                String[] types = params.getAcceptTypes();
                java.util.ArrayList<String> valid = new java.util.ArrayList<>();
                for (String type : types) if (type != null && type.matches("[\\w.+-]+/[\\w.+*-]+")) valid.add(type);
                if (!valid.isEmpty()) pick.putExtra(Intent.EXTRA_MIME_TYPES, valid.toArray(new String[0]));
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                try { startActivityForResult(Intent.createChooser(pick, getString(R.string.choose_files)), FILE_REQUEST); }
                catch (ActivityNotFoundException exception) { cancelFileChooser(source); toast(R.string.no_handler); }
                return true;
            }
        });
        view.setDownloadListener((url, agent, disposition, type, length) -> toast(R.string.download_unavailable));
        return view;
    }

    private boolean route(String url, boolean fromChat, boolean explicit) {
        // Ordinary in-page navigation stays in chat. A deliberate new window (including
        // a preview served by the same Relay) must preserve the chat document and draft.
        if (UrlPolicy.isSameOrigin(server, url) && fromChat && !explicit) return false;
        if (UrlPolicy.isHttps(url)) {
            if (fromChat || explicit) { openBrowser(url); return true; }
            return false;
        }
        if (UrlPolicy.isExternalScheme(url)) {
            new AlertDialog.Builder(this).setMessage(R.string.external_confirm)
                    .setNegativeButton(R.string.cancel, null)
                    .setPositiveButton(R.string.open, (dialog, which) -> launchExternal(url)).show();
        } else toast(R.string.unsupported_link);
        return true;
    }

    private void destroyRoutingView(WebView view) {
        if (routingViews.remove(view)) view.destroy();
    }

    private WebView visibleWebView() { return browser != null ? browser : chat; }

    private void openBrowser(String url) {
        if (browser == null) {
            browser = createWebView(false);
            pages.addView(browser, new FrameLayout.LayoutParams(-1, -1));
        }
        if (chat != null) chat.setVisibility(View.GONE);
        browser.loadUrl(url);
        refreshToolbar();
    }

    private void closeBrowser() {
        if (browser != null) {
            cancelFileChooser(browser);
            pages.removeView(browser);
            browser.destroy();
            browser = null;
        }
        pageFailed = false;
        failedView = null;
        failedUrl = null;
        error.setVisibility(View.GONE);
        progress.setVisibility(View.GONE);
        if (chat != null) chat.setVisibility(View.VISIBLE);
        refreshToolbar();
    }

    private void refreshToolbar() {
        toolbar.setVisibility(browser != null || chat == null || shellScript == null || pageFailed
                ? View.VISIBLE : View.GONE);
        String url = browser == null ? server : browser.getUrl();
        address.setText(url == null ? getString(R.string.toolbar_brand) : Uri.parse(url).getAuthority());
        address.setContentDescription(url);
        back.setVisibility(browser == null ? View.GONE : View.VISIBLE);
    }

    private void showFailure(WebView source, String url) {
        if (source != visibleWebView()) return;
        pageFailed = true;
        failedView = source;
        failedUrl = url;
        error.setText(R.string.load_error_retry);
        error.setVisibility(View.VISIBLE);
        progress.setVisibility(View.GONE);
        toolbar.setVisibility(View.VISIBLE);
    }

    private void showClientSettings() {
        if (isFinishing() || isDestroyed() || browser != null) return;
        new AlertDialog.Builder(this).setTitle(R.string.client_settings)
                .setItems(new String[]{getString(R.string.reload), getString(R.string.server), getString(R.string.about)},
                        (dialog, which) -> {
                            if (which == 0 && chat != null) chat.reload();
                            else if (which == 1) confirmServerChange();
                            else if (which == 2) showAbout();
                        })
                .setNegativeButton(R.string.cancel, null).show();
    }

    private void confirmServerChange() {
        if (changingServer) return;
        new AlertDialog.Builder(this).setMessage(R.string.change_warning)
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.change, (dialog, which) -> {
                    closeBrowser();
                    if (chat != null) { cancelFileChooser(chat); pages.removeView(chat); chat.destroy(); chat = null; }
                    showSetup();
                }).show();
    }

    private void showAbout() {
        PackageInfo provider = WebView.getCurrentWebViewPackage();
        String version;
        try { version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName; }
        catch (android.content.pm.PackageManager.NameNotFoundException exception) { version = "?"; }
        new AlertDialog.Builder(this).setTitle(R.string.app_name)
                .setMessage(getString(R.string.about_detail, version, provider == null ? "—" : provider.versionName))
                .setPositiveButton(android.R.string.ok, null).show();
    }

    private void showMenu(View anchor) {
        PopupMenu menu = new PopupMenu(this, anchor);
        if (visibleWebView() != null) {
            menu.getMenu().add(R.string.reload).setOnMenuItemClickListener(item -> { visibleWebView().reload(); return true; });
        }
        if (browser != null) {
            menu.getMenu().add(R.string.close_browser).setOnMenuItemClickListener(item -> { closeBrowser(); return true; });
            menu.getMenu().add(R.string.open_browser).setOnMenuItemClickListener(item -> { launchExternal(browser.getUrl()); return true; });
        }
        menu.getMenu().add(R.string.server).setOnMenuItemClickListener(item -> {
            confirmServerChange();
            return true;
        });
        menu.getMenu().add(R.string.about).setOnMenuItemClickListener(item -> {
            showAbout();
            return true;
        });
        menu.show();
    }

    private void launchExternal(String url) {
        if (url == null || (!UrlPolicy.isHttps(url) && !UrlPolicy.isExternalScheme(url))) return;
        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
        intent.addCategory(Intent.CATEGORY_BROWSABLE);
        try { startActivity(intent); } catch (ActivityNotFoundException exception) { toast(R.string.no_handler); }
    }

    private void toast(int message) { Toast.makeText(this, message, Toast.LENGTH_LONG).show(); }

    private void cancelFileChooser(WebView owner) {
        if (fileOwner == owner && fileCallback != null) {
            ValueCallback<Uri[]> callback = fileCallback;
            fileCallback = null;
            fileOwner = null;
            callback.onReceiveValue(null);
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != FILE_REQUEST || fileCallback == null) return;
        Uri[] selected = WebChromeClient.FileChooserParams.parseResult(result, data);
        // Document providers grant content URIs for this explicit selection. Do not let an
        // arbitrary picker return file:// paths into the application's private storage.
        if (selected != null) {
            for (Uri uri : selected) if (uri == null || !"content".equals(uri.getScheme())) { selected = null; break; }
        }
        ValueCallback<Uri[]> callback = fileCallback;
        fileCallback = null;
        fileOwner = null;
        callback.onReceiveValue(selected);
    }

    private void goBack() {
        if (browser != null) {
            if (browser.canGoBack()) browser.goBack(); else closeBrowser();
        } else if (chat != null && chat.canGoBack()) chat.goBack();
        else moveTaskToBack(true);
    }

    @SuppressWarnings("deprecation")
    // API 33+ uses the native OnBackInvokedCallback registered in onCreate. This is the
    // fallback for older Android/compatibility environments only.
    @SuppressLint("GestureBackNavigation")
    @Override public void onBackPressed() { goBack(); }

    @Override public boolean dispatchTouchEvent(MotionEvent event) {
        if (Build.VERSION.SDK_INT >= 35) {
            if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
                root.removeCallbacks(resetFrameRate);
                WebView view = visibleWebView();
                if (view != null) view.setRequestedFrameRate(View.REQUESTED_FRAME_RATE_CATEGORY_HIGH);
            } else if (event.getActionMasked() == MotionEvent.ACTION_UP
                    || event.getActionMasked() == MotionEvent.ACTION_CANCEL) {
                root.postDelayed(resetFrameRate, 700);
            }
        }
        return super.dispatchTouchEvent(event);
    }

    @Override protected void onPause() {
        root.removeCallbacks(resetFrameRate);
        resetFrameRate.run();
        if (chat != null) chat.onPause();
        if (browser != null) browser.onPause();
        if (chat != null || browser != null) CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override protected void onResume() {
        super.onResume();
        if (chat != null) chat.onResume();
        if (browser != null) browser.onResume();
        // The web client's visibility/WebSocket recovery owns reconnection. Never reload here:
        // a document chooser or brief app switch must not erase drafts or duplicate submissions.
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        if (chat != null) { Bundle saved = new Bundle(); chat.saveState(saved); state.putBundle("chat", saved); }
        super.onSaveInstanceState(state);
    }

    @Override protected void onDestroy() {
        root.removeCallbacks(resetFrameRate);
        for (WebView view : new java.util.ArrayList<>(routingViews)) destroyRoutingView(view);
        if (fileCallback != null) cancelFileChooser(fileOwner);
        if (browser != null) { pages.removeView(browser); browser.destroy(); }
        if (chat != null) { pages.removeView(chat); chat.destroy(); }
        super.onDestroy();
    }
}
