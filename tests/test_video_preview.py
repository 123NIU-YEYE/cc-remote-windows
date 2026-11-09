"""Video artifacts are sniffed by container header and keep their original bytes."""
from __future__ import annotations

import pytest

from cc_remote.wrapper.machine import WrapperMachine


CONTAINERS = {
    ".mp4": b"\0\0\0\x18ftypisom\0\0\0\0isomiso2",
    ".m4v": b"\0\0\0\x18ftypM4V \0\0\0\0M4V isom",
    ".mov": b"\0\0\0\x14ftypqt  \0\0\0\0qt  ",
    # Matroska/EBML, the real WebM header (not a RIFF chunk).
    ".webm": b"\x1a\x45\xdf\xa3" + b"\0" * 16,
    ".ogv": b"OggS\0" + b"\0" * 22,
}


@pytest.mark.parametrize("suffix,media_type", WrapperMachine.VIDEO_PREVIEW_MEDIA_TYPES.items())
def test_video_preview_identifies_container_and_preserves_bytes(tmp_path, suffix, media_type):
    path = tmp_path / ("clip" + suffix.upper())
    path.write_bytes(CONTAINERS[suffix])
    result = WrapperMachine._read_file_preview(str(tmp_path), path.name)
    assert result["format"] == "video"
    assert result["media_type"] == media_type
    assert result["data"] == CONTAINERS[suffix]
    assert result["size"] == path.stat().st_size


@pytest.mark.parametrize("suffix", WrapperMachine.VIDEO_PREVIEW_MEDIA_TYPES)
def test_video_preview_rejects_non_video_bytes(tmp_path, suffix):
    (tmp_path / ("fake" + suffix)).write_bytes(b"<html>not video</html>")
    with pytest.raises(ValueError, match="格式不匹配"):
        WrapperMachine._read_file_preview(str(tmp_path), "fake" + suffix)


def test_video_preview_rejects_riff_disguised_as_webm(tmp_path):
    (tmp_path / "fake.webm").write_bytes(b"RIFF\0\0\0\0WEBM" + b"\0" * 8)
    with pytest.raises(ValueError, match="格式不匹配"):
        WrapperMachine._read_file_preview(str(tmp_path), "fake.webm")
