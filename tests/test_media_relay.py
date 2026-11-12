"""HTTP media is authenticated for every range and stays outside chat replay."""

import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import asyncio

from cc_remote.relay.media import MediaRelay
from cc_remote.viewer import resource_headers
from cc_remote.media import MAX_MEDIA_BYTES
from cc_remote.relay.viewer import PendingRead, ViewerPeer


@pytest.fixture
def api(tmp_path):
    path = tmp_path / "sample.bin"
    path.write_bytes(b"0123456789")

    class Peer:
        closed = False
        media = True
        cancelled = []
        metadata_calls = []

        async def metadata(self, action, payload):
            self.metadata_calls.append((action, payload))
            return (
                {
                    "id": uuid4().hex,
                    "name": "测试视频.mp4",
                    "size": 10,
                    "media_type": "video/mp4",
                }
                if action == "media_open"
                else {"ok": True}
            )

        async def read(self, site, requested_path, method, headers, **kwargs):
            assert site is None and requested_path == ""
            status, response, self.start, self.remaining = resource_headers(
                path.stat(), "video/mp4", headers
            )
            return uuid4().hex, status, response

        async def cancel(self, identity):
            self.cancelled.append(identity)

        async def stream(self, identity, authorize):
            try:
                await authorize()
                yield path.read_bytes()[self.start : self.start + self.remaining]
            finally:
                await self.cancel(identity)

    peer = Peer()
    active = {"a": True, "b": True}
    viewers = SimpleNamespace(peers={"device-a": peer})
    app = FastAPI()

    async def claims(req):
        identity = req.cookies.get("session")
        return SimpleNamespace(jti=identity) if active.get(identity) else None

    async def allowed(claims, machine):
        return claims.jti == "a" and machine == "device-a"

    async def read_json(req, limit):
        body = await req.body()
        assert len(body) <= limit
        return json.loads(body)

    relay = MediaRelay(
        app,
        viewers,
        claims,
        lambda _: True,
        allowed,
        lambda req: req.headers.get("origin") in {None, "https://app.test"},
        lambda claims: "owner-" + claims.jti,
        read_json,
    )
    with TestClient(
        app, base_url="https://app.test", headers={"Origin": "https://app.test"}
    ) as client:
        client.cookies.set("session", "a")
        yield client, relay, peer, active


def open_file(client, **extra):
    return client.post(
        "/api/media/open",
        json={
            "machine_id": "device-a",
            "sid": "session",
            "path": "/private/clip.mp4",
            "download": False,
            "engine": "claude",
            "space": "code",
            **extra,
        },
    )


def test_video_ranges_and_head_are_exact_and_cancel_device_reads(api):
    client, _, peer, _ = api
    response = open_file(client)
    assert response.status_code == 200
    url = response.json()["url"]
    response = client.get(url, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["content-length"] == "4"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert "sandbox" in response.headers["content-security-policy"]
    assert peer.cancelled
    head = client.head(url)
    assert (
        head.status_code == 200
        and head.content == b""
        and head.headers["content-length"] == "10"
    )
    assert client.get(url, headers={"Range": "bytes=100-"}).status_code == 416


def test_other_account_machine_cross_origin_and_revocation_fail_closed(api):
    client, relay, _, active = api
    url = open_file(client).json()["url"]
    client.cookies.set("session", "b")
    assert client.get(url).status_code == 403
    client.cookies.set("session", "a")
    assert open_file(client, machine_id="device-b").status_code == 403
    assert client.get(url, headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.get(url, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    active["a"] = False
    assert client.get(url).status_code == 401
    assert len(relay.entries) == 1


def test_download_uses_safe_filename_and_media_id_is_not_a_credential(api):
    client, _, _, _ = api
    url = open_file(client, download=True).json()["url"]
    result = client.get(url)
    assert result.headers["content-type"] == "application/octet-stream"
    assert result.headers["content-disposition"].startswith(
        "attachment; filename*=UTF-8''"
    )
    client.cookies.clear()
    assert client.get(url).status_code == 401
    assert client.delete(url).status_code == 401


def test_wrapper_generation_and_explicit_close_revoke_old_media(api):
    client, relay, peer, _ = api
    url = open_file(client).json()["url"]
    assert client.delete(url).status_code == 200
    assert client.get(url).status_code == 404
    assert peer.metadata_calls[-1][0] == "media_close"
    url = open_file(client).json()["url"]
    relay.viewers.peers["device-a"] = object()
    assert client.get(url).status_code == 403


@pytest.mark.parametrize(
    "extra",
    [
        {"owner": "forged"},
        {"engine": {}},
        {"space": []},
        {"download": "yes"},
        {"path": "\0"},
        {"sid": "../session"},
    ],
)
def test_invalid_metadata_cannot_reach_wrapper(api, extra):
    client, _, peer, _ = api
    assert open_file(client, **extra).status_code == 400
    assert peer.metadata_calls == []


def test_binary_channel_allows_large_media_without_raising_static_publication_limit():
    async def run():
        identity = "a" * 32

        class Socket:
            def __init__(self):
                self.received = False

            async def receive(self):
                if self.received:
                    return {"type": "websocket.disconnect"}
                self.received = True
                return {
                    "type": "websocket.receive",
                    "text": json.dumps(
                        {
                            "type": "response",
                            "request_id": identity,
                            "status": 200,
                            "headers": {
                                "content-type": "video/mp4",
                                "content-length": str(500 * 1024 * 1024),
                            },
                        }
                    ),
                }

        peer = ViewerPeer(Socket(), {})
        read = PendingRead(head=True, max_bytes=MAX_MEDIA_BYTES)
        peer.pending[identity] = read
        await peer.receive()
        status, headers = read.metadata.result()
        assert status == 200 and int(headers["content-length"]) > 256 * 1024 * 1024
        assert read.chunks.qsize() <= 1

        static = ViewerPeer(Socket(), {})
        static.pending[identity] = PendingRead(head=True)
        # Observe the Future exception during failed socket cleanup as well.
        static.pending[identity].metadata.add_done_callback(
            lambda future: future.exception()
        )
        with pytest.raises(ValueError, match="size"):
            await static.receive()

    asyncio.run(run())
