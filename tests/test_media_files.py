"""Native file leases preserve byte ranges, scope, identity and bounded memory."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace

import pytest

from cc_remote.wrapper.machine import WrapperMachine
from cc_remote.wrapper.media_files import MediaFiles
from cc_remote.wrapper.preview_capabilities import PreviewCapabilityStore


@pytest.fixture
def media(tmp_path):
    ctx = SimpleNamespace(
        key="session",
        session_id="session",
        cwd=str(tmp_path),
        engine="claude",
        space="code",
        btw=False,
        preview_snapshot_token="generation",
        owner_client_id="owner",
    )
    store = PreviewCapabilityStore(tmp_path / "state")

    class Machine:
        instance_id = "wrapper-generation"
        contexts = {"session": ctx}
        _preview_capability_store = store
        _read_session_file = staticmethod(WrapperMachine._read_session_file)
        _path_is_below = staticmethod(WrapperMachine._path_is_below)

        def _ctx_for(self, sid):
            return self.contexts.get(sid)

        def _ctx_wire_sid(self, value):
            return value.key

        def _preview_capabilities(self, value):
            return store.snapshot(value.engine, value.space, value.key)

        async def _run_preview_capability_mutation(self, function, *args, **kwargs):
            return await asyncio.to_thread(function, *args, **kwargs)

        async def _viewer_page_scope(self, scope):
            if (
                scope.sid != "session"
                or scope.engine != ctx.engine
                or scope.space != ctx.space
            ):
                raise ValueError("invalid scope")
            return scope, ctx.cwd

    manager = MediaFiles(Machine())
    yield manager, ctx
    manager.close()


def request(path, **extra):
    return dict(
        sid="session",
        path=path,
        owner="owner",
        download=False,
        engine="claude",
        space="code",
        **extra,
    )


def test_large_video_is_opened_without_loading_the_whole_file(media, tmp_path):
    manager, _ = media
    path = tmp_path / "clip.mp4"
    with path.open("wb") as stream:
        stream.write(b"\0\0\0\x18ftypisom")
        stream.seek(32 * 1024 * 1024)
        stream.write(b"video-end")
    result = asyncio.run(manager.create(request(str(path))))
    assert result["size"] > 20 * 1024 * 1024
    assert result["media_type"] == "video/mp4"
    stream, _, _ = manager.open(result["id"], "owner")
    stream.seek(result["size"] - 9)
    assert stream.read(9) == b"video-end"
    stream.close()


def test_parallel_ranges_have_independent_positions_and_binary_bytes(media, tmp_path):
    manager, _ = media
    data = bytes(range(256)) * 2048
    (tmp_path / "data.bin").write_bytes(data)
    payload = request("data.bin")
    payload["download"] = True
    result = asyncio.run(manager.create(payload))

    def read(offset):
        stream, _, _ = manager.open(result["id"], "owner")
        stream.seek(offset)
        try:
            return stream.read(8192)
        finally:
            stream.close()

    offsets = [17, 901, 0, 99999, 47, 34567] * 10
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(read, offsets)) == [data[x : x + 8192] for x in offsets]


def test_file_change_and_session_replacement_revoke_reads(media, tmp_path):
    manager, ctx = media
    path = tmp_path / "video.mp4"
    path.write_bytes(b"\0\0\0\x18ftypisom")
    result = asyncio.run(manager.create(request("video.mp4")))
    with pytest.raises(PermissionError):
        manager.open(result["id"], "another-owner")
    stream, _, _ = manager.open(result["id"], "owner")
    path.write_bytes(b"changed bytes with different size")
    with pytest.raises(PermissionError, match="变化"):
        stream.read(10)
    ctx.preview_snapshot_token = "replacement"
    with pytest.raises(PermissionError, match="会话"):
        manager.open(result["id"], "owner")


def test_external_file_grant_is_exact_and_work_cannot_escape(media, tmp_path):
    manager, ctx = media
    outside = tmp_path.parent / "outside-media.bin"
    outside.write_bytes(b"private bytes")
    payload = request(str(outside))
    payload["download"] = True
    asyncio.run(manager.create(payload))
    grants = manager.machine._preview_capabilities(ctx)
    assert set(grants) == {os.path.realpath(outside)}
    ctx.space = "work"
    payload["space"] = "work"
    with pytest.raises(ValueError):
        asyncio.run(manager.create(payload))


def test_private_sidechat_cannot_be_read_by_another_account(media, tmp_path):
    manager, ctx = media
    ctx.btw = True
    ctx.owner_client_id = "private-owner"
    (tmp_path / "clip.mp4").write_bytes(b"\0\0\0\x18ftypisom")
    with pytest.raises(PermissionError):
        asyncio.run(manager.create(request("clip.mp4")))


def test_cold_history_file_read_does_not_create_an_engine(media, tmp_path):
    manager, _ = media
    manager.machine.contexts.clear()
    (tmp_path / "clip.mp4").write_bytes(b"\0\0\0\x18ftypisom")
    result = asyncio.run(manager.create(request("clip.mp4")))
    assert not manager.machine.contexts
    stream, _, _ = manager.open(result["id"], "owner")
    assert stream.read(12).endswith(b"ftypisom")


def test_unknown_container_is_downloadable_and_release_closes_descriptor(
    media, tmp_path
):
    manager, _ = media
    (tmp_path / "clip.mp4").write_bytes(b"<html>not a video</html>")
    with pytest.raises(ValueError, match="下载"):
        asyncio.run(manager.create(request("clip.mp4")))
    payload = request("clip.mp4")
    payload["download"] = True
    result = asyncio.run(manager.create(payload))
    stream, _, _ = manager.open(result["id"], "owner")
    fd = stream.fileno()
    manager.release(result["id"], "owner")
    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.parametrize(
    "path", ["//server/share/file.mp4", "\\\\server\\share\\file.mp4"]
)
def test_network_shares_are_never_opened(media, path):
    manager, _ = media
    with pytest.raises(ValueError, match="本机"):
        asyncio.run(manager.create(request(path)))
