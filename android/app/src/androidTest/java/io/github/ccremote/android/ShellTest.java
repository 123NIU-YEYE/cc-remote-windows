package io.github.ccremote.android;

import android.app.Instrumentation;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import org.json.JSONArray;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.Assert.*;

/** Offline renderer tests: fixture HTML is injected only by instrumentation, never production. */
@RunWith(AndroidJUnit4.class)
public class ShellTest {
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private MainActivity activity;
    private WebView chat;

    @Before public void openFixture() throws Exception {
        instrumentation.getTargetContext().getSharedPreferences("MainActivity", 0).edit()
                .clear().putString("server", "https://fixture.invalid").commit();
        Intent launch = new Intent(instrumentation.getTargetContext(), MainActivity.class);
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        activity = (MainActivity) instrumentation.startActivitySync(launch);
        instrumentation.runOnMainSync(() -> {
            chat = findVisibleWebView(activity.getWindow().getDecorView());
            assertNotNull(chat);
            chat.stopLoading();
            chat.loadDataWithBaseURL("https://fixture.invalid/",
                    "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                    + "<body><p>Offline shell fixture</p><input id='draft' value='keep this draft'>"
                    + "<p><a id='link' href='https://outside.invalid/page' target='_blank'>External page</a></p>"
                    + "<input id='file' type='file' multiple><p id='selection'>none</p>"
                    + "<script>file.onchange=()=>selection.textContent=file.files.length;</script>"
                    + "</body></html>", "text/html", "UTF-8", null);
        });
        awaitJs(chat, "document.getElementById('draft')?.value", "\"keep this draft\"");
    }

    @After public void closeFixture() {
        if (activity != null) instrumentation.runOnMainSync(activity::finish);
    }

    @Test public void externalPopupReturnsToSameChatAndDraft() throws Exception {
        assertPopupReturnsToChat();
    }

    @Test public void sameOriginPreviewAlsoPreservesChat() throws Exception {
        js(chat, "document.getElementById('link').href='https://fixture.invalid/preview'");
        assertPopupReturnsToChat();
    }

    private void assertPopupReturnsToChat() throws Exception {
        tap(chat, "link");
        long deadline = SystemClock.uptimeMillis() + 5000;
        AtomicReference<WebView> visible = new AtomicReference<>();
        do {
            instrumentation.runOnMainSync(() -> visible.set(findVisibleWebView(activity.getWindow().getDecorView())));
            if (visible.get() != null && visible.get() != chat) break;
            Thread.sleep(50);
        } while (SystemClock.uptimeMillis() < deadline);
        assertNotSame("External page must use a separate WebView", chat, visible.get());
        assertNotNull(visible.get());
        instrumentation.runOnMainSync(activity::onBackPressed);
        instrumentation.runOnMainSync(() -> assertSame(chat, findVisibleWebView(activity.getWindow().getDecorView())));
        assertEquals("\"keep this draft\"", js(chat, "document.getElementById('draft').value"));
    }

    @Test public void rotationKeepsDocumentAndDraft() throws Exception {
        instrumentation.runOnMainSync(() -> activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE));
        instrumentation.waitForIdleSync();
        awaitJs(chat, "document.getElementById('draft')?.value", "\"keep this draft\"");
        instrumentation.runOnMainSync(() -> assertSame(chat, findVisibleWebView(activity.getWindow().getDecorView())));
        instrumentation.runOnMainSync(() -> activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT));
    }

    @Test public void filePickerCanCancelAndReopen() throws Exception {
        tap(chat, "file");
        instrumentation.waitForIdleSync();
        Thread.sleep(600);
        instrumentation.sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
        instrumentation.waitForIdleSync();
        Thread.sleep(300);
        tap(chat, "file");
        Thread.sleep(600);
        instrumentation.sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
        instrumentation.waitForIdleSync();
        assertEquals("\"keep this draft\"", js(chat, "document.getElementById('draft').value"));
        assertEquals("0", js(chat, "document.getElementById('file').files.length"));
    }

    private static WebView findVisibleWebView(View view) {
        if (view.getVisibility() != View.VISIBLE) return null;
        if (view instanceof WebView) return (WebView) view;
        if (view instanceof ViewGroup group) {
            for (int i = group.getChildCount() - 1; i >= 0; i--) {
                WebView found = findVisibleWebView(group.getChildAt(i));
                if (found != null) return found;
            }
        }
        return null;
    }

    private String js(WebView view, String script) throws Exception {
        AtomicReference<String> result = new AtomicReference<>();
        CountDownLatch latch = new CountDownLatch(1);
        instrumentation.runOnMainSync(() -> view.evaluateJavascript(script, value -> { result.set(value); latch.countDown(); }));
        assertTrue("WebView script timed out", latch.await(5, TimeUnit.SECONDS));
        return result.get();
    }

    private void awaitJs(WebView view, String script, String expected) throws Exception {
        long deadline = SystemClock.uptimeMillis() + 5000;
        String result;
        do {
            result = js(view, script);
            if (expected.equals(result)) return;
            Thread.sleep(50);
        } while (SystemClock.uptimeMillis() < deadline);
        assertEquals(expected, result);
    }

    private void tap(WebView view, String id) throws Exception {
        JSONArray rect = new JSONArray(js(view, "(()=>{let r=document.getElementById('" + id
                + "').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2,devicePixelRatio]})()"));
        int[] location = new int[2];
        instrumentation.runOnMainSync(() -> view.getLocationOnScreen(location));
        float x = location[0] + (float) (rect.getDouble(0) * rect.getDouble(2));
        float y = location[1] + (float) (rect.getDouble(1) * rect.getDouble(2));
        long time = SystemClock.uptimeMillis();
        MotionEvent down = MotionEvent.obtain(time, time, MotionEvent.ACTION_DOWN, x, y, 0);
        MotionEvent up = MotionEvent.obtain(time, time + 50, MotionEvent.ACTION_UP, x, y, 0);
        instrumentation.sendPointerSync(down);
        instrumentation.sendPointerSync(up);
        down.recycle(); up.recycle();
    }
}
