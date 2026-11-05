"""Bounded HTML prologue handling for the isolated preview keyboard helper."""
import pytest

from cc_remote.relay.viewer_html import preview_html


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding,bom", [
    ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
])
@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 65536])
@pytest.mark.parametrize("markup", ["", "<!doctype html><p>中文 preview</p>"])
async def test_preview_preserves_utf16_bytes_without_injecting_ascii(encoding, bom, chunk_size, markup):
    source = bom + markup.encode(encoding)

    async def chunks():
        for start in range(0, len(source), chunk_size):
            yield source[start:start + chunk_size]

    actual = b"".join([part async for part in preview_html(chunks(), b"<script>helper()</script>")])
    assert actual == source


@pytest.mark.asyncio
@pytest.mark.parametrize("prologue", [
    b"", b"\xef\xbb\xbf", b" \n\t", b"<!doctype html>",
    b'<!DOCTYPE HTML PUBLIC "legacy>id" "system">',
    b"<!-- before --><!doctype html><!-- after -->",
    b"<!--><!---><!-- odd --!><!doctype html>",
    b'<?xml version="1.0"?><!doctype html>',
    b"\xef\xbb\xbf\n<!-- comment --><!doctype html>\n",
])
@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 65536])
async def test_preview_helper_preserves_prologue_and_precedes_page_csp(prologue, chunk_size):
    content = (b'<html lang="zh"><head><meta charset="utf-8">'
               b'<meta http-equiv="Content-Security-Policy" content="script-src \'none\'">'
               b'</head><body>\xe4\xb8\xad\xe6\x96\x87</body></html>')
    source = prologue + content

    async def chunks():
        for start in range(0, len(source), chunk_size):
            yield source[start:start + chunk_size]

    helper = b"<script>trustedKeyboardHelper()</script>"
    actual = b"".join([part async for part in preview_html(chunks(), helper)])
    assert actual == prologue + helper + content


@pytest.mark.asyncio
async def test_preview_prologue_streams_large_comments_without_waiting_for_their_end():
    pulled = 0
    emitted = 0
    block = b"x" * 65536
    helper = b"<script>trustedKeyboardHelper()</script>"

    async def chunks():
        nonlocal pulled
        for chunk in [b"<!--", *([block] * 32), b"--><!doctype html><p>hello</p>"]:
            pulled += len(chunk)
            yield chunk

    output = []
    async for part in preview_html(chunks(), helper):
        output.append(part)
        emitted += len(part)
        assert pulled - emitted <= 65545
    assert b"".join(output) == b"<!--" + block * 32 + b"--><!doctype html>" + helper + b"<p>hello</p>"
