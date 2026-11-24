"""A preview invalidation marker other users can access is never trusted."""
from uuid import uuid4

from cc_remote.wrapper.preview_capabilities import PreviewCapabilityStore
from tests.platform_support import make_public


def _abandoned_marker(state_dir):
    marker = state_dir / ".preview-capabilities.invalidated"
    marker.write_text(uuid4().hex + "\n", encoding="utf-8")
    marker.chmod(0o600)  # the usual umask leaves new files group/world readable
    return marker


def test_private_abandoned_marker_is_recovered(tmp_path):
    marker = _abandoned_marker(tmp_path)
    assert PreviewCapabilityStore(tmp_path)._persistent is True
    assert not marker.exists()


def test_public_abandoned_marker_fails_closed(tmp_path):
    marker = _abandoned_marker(tmp_path)
    make_public(marker, 0o644)
    assert PreviewCapabilityStore(tmp_path)._persistent is False
    assert marker.exists()
