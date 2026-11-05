"""Native descriptor reads must preserve transcript and image bytes."""
import os


def test_default_descriptor_io_preserves_crlf_ctrl_z_and_binary_bytes(tmp_path):
    path = tmp_path / "source.bin"
    payload = b"\xef\xbb\xbfline\r\n\x1a\x00\xffend\r\n"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        assert os.write(descriptor, payload) == len(payload)
    finally:
        os.close(descriptor)
    assert path.read_bytes() == payload
    descriptor = os.open(path, os.O_RDONLY)
    try:
        assert os.read(descriptor, len(payload) + 1) == payload
    finally:
        os.close(descriptor)
