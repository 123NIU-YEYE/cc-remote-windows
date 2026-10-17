package io.github.ccremote.android;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** The shell never broadens the relay's cookie or WebSocket origin boundary. */
final class UrlPolicy {
    private UrlPolicy() {}

    static String serverOrigin(String input) {
        URI uri = parse(input == null ? "" : input.trim());
        if (uri == null || !"https".equalsIgnoreCase(uri.getScheme())
                || uri.getRawUserInfo() != null || uri.getRawQuery() != null
                || uri.getRawFragment() != null
                || !("".equals(uri.getRawPath()) || "/".equals(uri.getRawPath()))) {
            throw new IllegalArgumentException("Invalid server origin");
        }
        String origin = origin(uri);
        if (origin == null) throw new IllegalArgumentException("Invalid server origin");
        return origin;
    }

    static boolean isHttps(String value) {
        URI uri = parse(value);
        return uri != null && "https".equalsIgnoreCase(uri.getScheme()) && origin(uri) != null;
    }

    static boolean isSameOrigin(String server, String value) {
        URI uri = parse(value);
        return server != null && uri != null && server.equals(origin(uri));
    }

    static boolean isSettingsRequest(String url, String server, String currentUrl,
                                     boolean mainFrame, boolean userGesture) {
        return "cc-remote-app://settings".equals(url) && mainFrame && userGesture
                && isSameOrigin(server, currentUrl);
    }

    static boolean isExternalScheme(String value) {
        URI uri = parse(value);
        if (uri == null || uri.getScheme() == null) return false;
        String scheme = uri.getScheme().toLowerCase(Locale.ROOT);
        return "mailto".equals(scheme) || "tel".equals(scheme)
                || ("http".equals(scheme) && origin(uri) != null);
    }

    private static URI parse(String value) {
        if (value == null || value.indexOf('\\') >= 0) return null;
        try { return new URI(value); } catch (URISyntaxException e) { return null; }
    }

    private static String origin(URI uri) {
        String scheme = uri.getScheme();
        String host = uri.getHost();
        int port = uri.getPort();
        if (scheme == null || host == null || host.isEmpty() || uri.getRawUserInfo() != null
                || port < -1 || port == 0 || port > 65535) return null;
        scheme = scheme.toLowerCase(Locale.ROOT);
        if (!"https".equals(scheme) && !"http".equals(scheme)) return null;
        String suffix = port == -1 || ("https".equals(scheme) && port == 443)
                || ("http".equals(scheme) && port == 80) ? "" : ":" + port;
        return scheme + "://" + host.toLowerCase(Locale.ROOT) + suffix;
    }
}
