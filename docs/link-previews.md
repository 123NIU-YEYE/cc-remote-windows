# Link previews

Message Markdown (including BTW) and Markdown file previews share one active
link popover. Moving to another link closes the previous popover immediately;
leaving all links allows 180 ms to move into the card. Local paths wrap at slash
boundaries within 360 px, remain selectable, and retain their exact copied text.

Public HTTP(S) links retain normal navigation. Their icon button opens a preview
on touch devices; hovering opens it on desktop. Arrow Down or activating the
icon with the keyboard enters the card; Escape closes it. Previews use fixed
portals, so loading metadata never changes the conversation layout. The rich
card and metadata client load separately from the initial application bundle.

`POST /api/link-preview` requires a live session cookie and the exact allowed
Origin. The Relay anonymously reads public metadata, without invoking a model
or contacting a Wrapper. This is separate from the authenticated Remote Viewer
and its local static-file publications.

- Only HTTP(S), ports 80/443, and URLs without credentials are accepted. Each
  redirect and each image request resolves DNS again, rejects non-public and
  mixed-public/private results, and connects to a validated IP with the original
  TLS hostname and Host header. Environment proxies and connection reuse across
  redirects are disabled. HTTPS redirects may not downgrade to HTTP.
- At most four hops are followed. HTML reads stop after the head or 256 KiB;
  source images are limited to 1 MiB and source icons to 256 KiB. These download
  budgets allow multi-size ICO files and social covers to be resized instead of
  rejecting them at the final thumbnail's byte cap. Recognized raster images
  are decoded only within a 4-million-pixel budget (1 million for icons) and a
  4096-pixel edge limit, including embedded ICO images and WebP canvases before
  eager decoder allocation. Only the first frame is decoded and re-encoded as a
  metadata-free static PNG, at most 1024 × 768 (128 × 128 for icons), still within
  256 KiB (64 KiB for icons). At most four bounded PNG encodes reduce dimensions
  further when needed to fit that output cap. Decoding uses at most two worker
  threads, keeping the control-link event loop responsive. Invalid or oversized images are omitted while page text
  remains available. SVG, HTML, scripts, cookies and external image URLs are
  never embedded in the returned card. CSP stays intact.
- Linked pages are fetched only on preview interaction. Visible inline favicons
  use an origin-only request, without the link's path, query or fragment. If
  `/favicon.ico` is unavailable, bounded homepage metadata discovers declared
  raster/touch icons, including after redirects. These background requests never
  fetch cover images. Discovered icons are shared with mounted same-origin links;
  a late failed request cannot replace a successfully discovered icon.
- Transparent, neutral single-color favicons use their alpha silhouette with the
  theme's foreground color in both message links and preview headers. This keeps
  dark logos visible on dark themes, including custom palettes. Colored icons,
  opaque backgrounds, and icons with internal color/brightness details retain
  their original pixels. Classification reads only bounded Relay PNGs locally;
  it makes no extra network request and falls back to the original on failure.
- The Relay permits four concurrent metadata jobs and 120 requests/minute per
  client IP. Browser requests are deduplicated and limited to three concurrent
  requests. Both sides retain at most 64 cache entries in memory, with bounded
  lifetimes (up to one hour, one minute for misses); nothing is written to disk.
- Unsupported sites, private URLs, timeouts and older Relays leave a normal link
  and a minimal card. A DNS setup returning virtual/private proxy addresses
  intentionally fails closed; the Relay needs public DNS results for previews.

Regression coverage: `tests/test_link_preview.py`, `tests/test_link_preview_images.py`, the Markdown preview unit
suite, and `local Markdown file` / `Markdown HTML preview` browser cases in
`web/tests/history-browser.spec.ts`. Tests stub network metadata and do not call
models or arbitrary live websites.
