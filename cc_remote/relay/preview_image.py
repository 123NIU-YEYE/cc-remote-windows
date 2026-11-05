"""Convert untrusted link images to bounded, metadata-free static thumbnails."""
from __future__ import annotations

import base64
import io

from PIL import IcoImagePlugin, Image

MAX_IMAGE_BYTES = 256 * 1024
MAX_ICON_BYTES = 64 * 1024
MAX_IMAGE_SOURCE_BYTES = 1024 * 1024
MAX_ICON_SOURCE_BYTES = 256 * 1024
MAX_SOURCE_PIXELS = 4_000_000
MAX_SOURCE_EDGE = 4096
MAX_ICON_PIXELS = 1_000_000
FORMATS = ("PNG", "JPEG", "GIF", "WEBP", "ICO")


def _check_size(size: tuple[int, int], pixels: int) -> None:
    width, height = size
    if (min(size) <= 0 or max(size) > MAX_SOURCE_EDGE or width * height > pixels):
        raise ValueError("Preview image dimensions exceed the decode limit")


def _check_eager_headers(content: bytes, pixels: int) -> None:
    # Most Pillow plugins are lazy. WebP allocates its animation canvas during
    # open(), and ICO loads its selected entry: check those headers beforehand.
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        kind, header = content[12:16], content[20:30]
        if kind == b"VP8X" and len(header) == 10:
            size = (1 + int.from_bytes(header[4:7], "little"),
                    1 + int.from_bytes(header[7:10], "little"))
        elif kind == b"VP8 " and len(header) == 10 and header[3:6] == b"\x9d\x01\x2a":
            size = (int.from_bytes(header[6:8], "little") & 0x3fff,
                    int.from_bytes(header[8:10], "little") & 0x3fff)
        elif kind == b"VP8L" and len(header) >= 5 and header[0] == 0x2f:
            bits = int.from_bytes(header[1:5], "little")
            size = (1 + (bits & 0x3fff), 1 + ((bits >> 14) & 0x3fff))
        else:
            raise ValueError("Unsupported WebP header")
        _check_size(size, pixels)
    elif content.startswith(b"\x00\x00\x01\x00"):
        count = int.from_bytes(content[4:6], "little")
        if not 0 < count <= 64 or len(content) < 6 + count * 16:
            raise ValueError("Invalid icon directory")
        directory = IcoImagePlugin.IcoFile(io.BytesIO(content))
        for entry in directory.entry:
            if entry.offset < 6 + count * 16 or entry.offset + entry.size > len(content):
                raise ValueError("Invalid icon entry")
            # Directory dimensions are only a hint; inspect the real embedded
            # PNG/DIB header without invoking the eager ICO decoder.
            with Image.open(io.BytesIO(content[entry.offset:entry.offset + entry.size]),
                            formats=("PNG", "DIB")) as frame:
                _check_size(frame.size, pixels)
                size = (frame.width, frame.height // 2) if frame.format == "DIB" else frame.size
                if size != entry.dim:
                    raise ValueError("Icon dimensions do not match its directory")


def raster_data(content: bytes, *, icon: bool = False) -> str | None:
    limit = MAX_ICON_BYTES if icon else MAX_IMAGE_BYTES
    source_limit = MAX_ICON_SOURCE_BYTES if icon else MAX_IMAGE_SOURCE_BYTES
    if not content or len(content) > source_limit:
        return None
    try:
        pixels = MAX_ICON_PIXELS if icon else MAX_SOURCE_PIXELS
        _check_eager_headers(content, pixels)
        with Image.open(io.BytesIO(content), formats=FORMATS) as source:
            _check_size(source.size, pixels)
            # Only decode the first frame. Never enumerate/convert an
            # animation or forward its compressed frames to the browser.
            with source.convert("RGBA") as thumbnail:
                thumbnail.thumbnail((128, 128) if icon else (1024, 768))
                thumbnail.info.clear()
                # Input bytes and output bytes have different budgets: a
                # multi-size ICO or large social image can make a small preview.
                # Bound encoding work as well as the final cached payload.
                for attempt in range(4):
                    output = io.BytesIO()
                    thumbnail.save(output, format="PNG")
                    normalized = output.getvalue()
                    if len(normalized) <= limit:
                        return "data:image/png;base64," + base64.b64encode(normalized).decode("ascii")
                    if attempt < 3:
                        thumbnail.thumbnail((max(1, thumbnail.width * 3 // 4),
                                             max(1, thumbnail.height * 3 // 4)))
    except (ValueError, OSError, SyntaxError, EOFError, IndexError, OverflowError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        pass
    return None
