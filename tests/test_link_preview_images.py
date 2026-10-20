"""Untrusted preview images must not delegate unbounded decoding to browsers."""
import base64
import io
import random
import struct

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from cc_remote.relay.link_preview import raster_data
from cc_remote.relay.preview_image import MAX_ICON_SOURCE_BYTES, MAX_IMAGE_SOURCE_BYTES


def encoded_image(size=(32, 32), *, format="PNG", **kwargs):
    output = io.BytesIO()
    with Image.new("RGB", size, "red") as image:
        image.save(output, format=format, **kwargs)
    return output.getvalue()


def decoded_preview(data):
    assert data is not None and data.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1])))


@pytest.mark.parametrize("format", ["PNG", "JPEG", "GIF", "WEBP"])
def test_compressed_large_raster_is_rejected_before_decode(format, monkeypatch):
    content = encoded_image((3000, 2000), format=format)
    assert len(content) < 256 * 1024

    def no_decode(*args, **kwargs):
        pytest.fail("Oversized image reached pixel decoding")

    monkeypatch.setattr(Image.Image, "load", no_decode)
    assert raster_data(content) is None


def test_large_embedded_ico_cannot_hide_behind_small_directory(monkeypatch):
    png = encoded_image((3000, 2000))
    content = (struct.pack("<HHH", 0, 1, 1)
               + struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, len(png), 22) + png)
    assert len(content) < 64 * 1024

    def no_decode(*args, **kwargs):
        pytest.fail("Oversized ICO entry reached pixel decoding")

    monkeypatch.setattr(Image.Image, "load", no_decode)
    assert raster_data(content) is None


@pytest.mark.parametrize("chunk,header", [
    (b"VP8X", b"\x02\0\0\0" + (9999).to_bytes(3, "little") * 2),
    (b"VP8 ", b"\0\0\0\x9d\x01\x2a" + struct.pack("<HH", 10000, 10000)),
    (b"VP8L", b"\x2f" + (9999 | 9999 << 14).to_bytes(4, "little")),
])
def test_webp_canvas_is_bounded_before_decoder_construction(chunk, header, monkeypatch):
    payload = b"WEBP" + chunk + struct.pack("<I", len(header)) + header
    content = b"RIFF" + struct.pack("<I", len(payload)) + payload

    def no_open(*args, **kwargs):
        pytest.fail("Oversized WebP reached eager decoder construction")

    monkeypatch.setattr(Image, "open", no_open)
    assert raster_data(content) is None


def test_animation_is_replaced_with_one_static_frame():
    frames = [Image.new("RGB", (16, 16), (i, 0, 0)) for i in range(200)]
    output = io.BytesIO()
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=1, loop=0)
    assert len(output.getvalue()) < 64 * 1024
    with decoded_preview(raster_data(output.getvalue())) as image:
        assert image.n_frames == 1
        assert image.size == (16, 16)


@pytest.mark.parametrize("format", ["PNG", "JPEG", "GIF", "WEBP", "ICO"])
def test_supported_rasters_become_static_png(format):
    with decoded_preview(raster_data(encoded_image(format=format))) as image:
        assert image.size == (32, 32)
        assert image.n_frames == 1


@pytest.mark.parametrize("format,options", [
    ("WEBP", {"lossless": True}), ("ICO", {"bitmap_format": "bmp"}),
])
def test_lossless_webp_and_bitmap_ico_remain_supported(format, options):
    with decoded_preview(raster_data(encoded_image(format=format, **options))) as image:
        assert image.size == (32, 32)


@pytest.mark.parametrize("format", ["PNG", "WEBP"])
def test_animated_png_and_webp_are_static(format):
    output = io.BytesIO()
    with Image.new("RGBA", (16, 16), "red") as first, Image.new("RGBA", (16, 16), "blue") as second:
        first.save(output, format=format, save_all=True, append_images=[second], duration=10, loop=0)
    with decoded_preview(raster_data(output.getvalue())) as image:
        assert image.n_frames == 1


@pytest.mark.parametrize("icon,expected", [(False, (1024, 512)), (True, (128, 64))])
def test_thumbnails_strip_metadata_and_limit_output_dimensions(icon, expected):
    metadata = PngInfo()
    metadata.add_text("private", "must not reach the browser")
    content = encoded_image((1200, 600), pnginfo=metadata)
    with decoded_preview(raster_data(content, icon=icon)) as image:
        assert image.size == expected
        assert not image.info


def test_icon_has_a_smaller_source_pixel_budget():
    content = encoded_image((1200, 1200))
    assert raster_data(content, icon=True) is None
    assert raster_data(content) is not None


def test_multi_size_bitmap_icon_can_exceed_its_thumbnail_budget():
    content = encoded_image((128, 128), format="ICO", bitmap_format="bmp")
    assert 64 * 1024 < len(content) < MAX_ICON_SOURCE_BYTES
    data = raster_data(content, icon=True)
    with decoded_preview(data) as image:
        assert image.size == (128, 128)
    assert len(base64.b64decode(data.split(",", 1)[1])) <= 64 * 1024


def test_large_cover_is_reduced_until_its_static_png_fits():
    pixels = random.Random(1).randbytes(600 * 400 * 3)
    with Image.frombytes("RGB", (600, 400), pixels) as image:
        output = io.BytesIO()
        image.save(output, format="PNG")
    content = output.getvalue()
    assert 256 * 1024 < len(content) < MAX_IMAGE_SOURCE_BYTES
    data = raster_data(content)
    with decoded_preview(data) as image:
        assert image.width < 600 and image.height < 400
        assert image.n_frames == 1 and not image.info
    assert len(base64.b64decode(data.split(",", 1)[1])) <= 256 * 1024


@pytest.mark.parametrize("icon,limit", [(False, MAX_IMAGE_SOURCE_BYTES), (True, MAX_ICON_SOURCE_BYTES)])
def test_source_byte_budget_is_checked_before_opening_image(icon, limit, monkeypatch):
    def no_open(*args, **kwargs):
        pytest.fail("Oversized source reached the image decoder")

    monkeypatch.setattr(Image, "open", no_open)
    assert raster_data(b"x" * (limit + 1), icon=icon) is None


def test_tall_thin_image_is_rejected():
    assert raster_data(encoded_image((1, 4097))) is None


def test_reencoded_output_still_obeys_byte_cap():
    # The JPEG fits the input cap but its lossless thumbnail exceeds the output
    # cap. Do not pass a much larger PNG to the browser/cache after conversion.
    pixels = random.Random(0).randbytes(1024 * 768 * 3)
    with Image.frombytes("RGB", (1024, 768), pixels) as image:
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=10)
    assert len(output.getvalue()) < 256 * 1024
    assert raster_data(output.getvalue()) is None


@pytest.mark.parametrize("content", [
    b"", b"\x89PNG\r\n\x1a\n", b"GIF89a", b"<svg/>",
    b"\0\0\1\0\xff\xff", b"RIFF\0\0\0\0WEBPVP8X", b"\xff\xd8\xff",
    b"x" * (256 * 1024 + 1),
])
def test_invalid_or_oversized_payloads_fail_closed(content):
    assert raster_data(content) is None
