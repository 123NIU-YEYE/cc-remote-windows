package io.github.ccremote.android;

import org.junit.Test;
import static org.junit.Assert.*;

public class UrlPolicyTest {
    @Test public void canonicalOriginPreservesExplicitPortsAndIpv6() {
        assertEquals("https://example.com", UrlPolicy.serverOrigin(" HTTPS://EXAMPLE.COM:443/ "));
        assertEquals("https://example.com:8443", UrlPolicy.serverOrigin("https://example.com:8443"));
        assertEquals("https://[::1]:8443", UrlPolicy.serverOrigin("https://[::1]:8443/"));
    }

    @Test public void rejectsAmbiguousAddressesAndSecrets() {
        for (String value : new String[]{"", "example.com", "http://example.com", "https://user:secret@example.com",
                "https://example.com/path", "https://example.com?token=secret", "https://example.com/#key",
                "https://example.com:0", "https://example.com:65536", "https://example.com\\@evil.com",
                "file:///tmp/page", "javascript:alert(1)", "https:///example.com", "https:example.com"}) {
            assertThrows(value, IllegalArgumentException.class, () -> UrlPolicy.serverOrigin(value));
        }
    }

    @Test public void matchesExactOriginWithoutBroadeningTrust() {
        String server = "https://example.com";
        assertTrue(UrlPolicy.isSameOrigin(server, "https://EXAMPLE.COM:443/api/login"));
        for (String value : new String[]{"https://example.com.evil/path", "https://evil/?https://example.com",
                "http://example.com", "https://example.com:8443", "https://example.com@evil/",
                "https://user@example.com", "file:///tmp", "about:blank", "blob:https://example.com/a"}) {
            assertFalse(value, UrlPolicy.isSameOrigin(server, value));
        }
    }

    @Test public void limitsExternalNavigation() {
        assertTrue(UrlPolicy.isHttps("https://docs.example.com/path?q=1#anchor"));
        assertTrue(UrlPolicy.isExternalScheme("mailto:hello@example.com"));
        assertTrue(UrlPolicy.isExternalScheme("tel:123"));
        assertFalse(UrlPolicy.isExternalScheme("intent://host/#Intent;scheme=foo;end"));
        assertFalse(UrlPolicy.isHttps("https://user:pass@example.com"));
        assertFalse(UrlPolicy.isHttps("javascript:alert(1)"));
    }

    @Test public void settingsRequireExactChatOriginAndTopLevelUserGesture() {
        String command = "cc-remote-app://settings";
        String server = "https://example.com";
        assertTrue(UrlPolicy.isSettingsRequest(command, server, server + "/", true, true));
        assertFalse(UrlPolicy.isSettingsRequest(command, server, "https://evil.example/", true, true));
        assertFalse(UrlPolicy.isSettingsRequest(command, server, server, false, true));
        assertFalse(UrlPolicy.isSettingsRequest(command, server, server, true, false));
        assertFalse(UrlPolicy.isSettingsRequest(command + "?action=logout", server, server, true, true));
        assertFalse(UrlPolicy.isSettingsRequest(command, server, null, true, true));
    }
}
