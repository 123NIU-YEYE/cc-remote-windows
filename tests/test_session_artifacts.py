"""Artifacts are scoped metadata and private copies, never a new model turn."""
import base64
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cc_remote.protocol import (
    AssistantMsgEnd, Delta, GetSessionArtifactPreview, GetSessionArtifacts,
    History, HistoryImage, SessionArtifacts, deserialize, serialize,
)
from cc_remote.wrapper.session_artifacts import SessionArtifactStore, image_thumbnail, local_links, public_entry
from tests.test_multisession import _mk_ctx, _mk_machine


def test_artifact_upload_survives_reopen_and_has_isolated_identity(tmp_path):
    store = SessionArtifactStore(tmp_path)
    files = [{"filename": "notes.txt", "data": base64.b64encode(b"keep me").decode()}]
    store.uploads("session-a", "msg", files)
    store.uploads("session-a", "msg", files)
    reopened = SessionArtifactStore(tmp_path)
    rows, _, more = reopened.page("session-a")
    assert len(rows) == 1 and not more
    assert Path(rows[0]["path"]).read_bytes() == b"keep me"
    assert Path(rows[0]["path"]).stat().st_mode & 0o777 == 0o600
    assert reopened.page("session-b")[0] == []
    assert reopened.get("session-b", rows[0]["id"]) is None
    assert public_entry(rows[0])["available"] is True


def test_artifact_pages_are_chronological_and_scoped(tmp_path):
    store = SessionArtifactStore(tmp_path)
    for i in range(95):
        store.add("a", str(i), f"{i}.md", f"/missing/{i}.md", "delivered", created_at=i)
    first, cursor, more = store.page("a")
    assert len(first) == 40 and more
    assert first[0]["name"] == "94.md"
    store.add("a", "historical", "old.md", "/old.md", "delivered", created_at=0)
    second, cursor2, more = store.page("a", cursor)
    assert len(second) == 40 and more
    assert set(r["id"] for r in first).isdisjoint(r["id"] for r in second)
    third, _, more = store.page("a", cursor2)
    assert len(third) == 16 and not more
    assert not public_entry(third[0])["available"]
    with pytest.raises(ValueError):
        store.page("b", cursor)


def test_rekey_and_delete_only_remove_owned_upload_copies(tmp_path):
    store = SessionArtifactStore(tmp_path)
    old = json.dumps(["machine", "codex", "code", "tmp-one"])
    new = json.dumps(["machine", "codex", "code", "account:sid"])
    source = tmp_path / "result.md"
    source.write_text("original")
    store.uploads(old, "msg", [{"filename": "a.txt", "data": "YQ=="}])
    store.text(old, f"[result]({source})", str(tmp_path))
    copied = next(r["path"] for r in store.page(old)[0] if r["source"] == "upload")
    store.move(old, new)
    assert len(store.page(old)[0]) == len(store.page(new)[0]) == 2
    store.text(old, f"[result]({source})", str(tmp_path))
    store.uploads(new, "msg", [{"filename": "a.txt", "data": "YQ=="}])
    assert len(list(store.root.glob("*-a.txt"))) == 1
    assert len(store.page(new)[0]) == 2
    store.drop_session("other-device", "codex", "account:sid")
    assert len(store.page(new)[0]) == 2
    store.drop_session("machine", "codex", "account:sid")
    assert not Path(copied).exists() and source.exists()
    assert store.page(new)[0] == []


def test_artifact_discovery_only_follows_explicit_local_destinations():
    text = '''[代码](</project/a b.py:12>) [网络](https://example.com/a.pdf)
    [相对](docs/report.md) [文件](file:///tmp/report.pdf)
    [锚点](#section) [远程](file://server/x.md)
    ```md
    [例子](/example.md)
    ```
    visualize{"path":"/output/page.html"}'''
    assert local_links(text, "/workspace") == [
        "/project/a b.py", "/workspace/docs/report.md", "/tmp/report.pdf", "/output/page.html"]


def test_artifact_root_symlink_is_rejected(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "session-artifacts").symlink_to(target)
    with pytest.raises(OSError):
        SessionArtifactStore(tmp_path)


@pytest.mark.asyncio
async def test_live_discovery_rekey_and_thinking_exclusion(tmp_path):
    machine, _ = _mk_machine()
    ctx = _mk_ctx("tmp-one")
    ctx.cwd = str(tmp_path)
    result = tmp_path / "answer.md"
    result.write_text("answer")
    service = machine._artifacts
    await service.observe(ctx, Delta(message_id="a", text=f"[结果]({result})"))
    await service.observe(ctx, AssistantMsgEnd(message_id="a", channel="final"))
    await service.observe(ctx, Delta(message_id="b", text="[secret](/secret.txt)", channel="thinking"))
    await service.observe(ctx, AssistantMsgEnd(message_id="b", channel="thinking"))
    rows, _, _ = service.store.page(service.context_scope(ctx))
    assert len(rows) == 1 and rows[0]["name"] == "answer.md"
    await machine._rekey_preview_session(ctx, "tmp-one", "real-one")
    assert len(service.store.page(service.context_scope(ctx, "tmp-one"))[0]) == 1
    assert len(service.store.page(service.context_scope(ctx, "real-one"))[0]) == 1


@pytest.mark.asyncio
async def test_catalog_reads_cache_first_then_one_bounded_history_page(tmp_path):
    machine, transport = _mk_machine()
    ctx = _mk_ctx("session", "session")
    ctx.cwd = str(tmp_path)
    machine.sessions[ctx.key] = ctx
    machine._history_page_singleflight = AsyncMock(return_value=History(
        session_id="session", revision="rev", detail="summary", oldest_id="older", has_more=True,
        turns=[{"id": "turn", "ts": 1000, "blocks": [{"kind": "text", "text": "[成果](result.md)"}],
                "imageRefs": [{"image_id": "img", "media_type": "image/png", "width": 1, "height": 1, "byte_size": 1}]}]))
    cmd = GetSessionArtifacts(sid="session", engine="claude", request_id="request", client_id="browser")
    result = await machine._handle_get_session_artifacts(cmd)
    assert result.entries == [] and result.to == "browser"
    machine._history_page_singleflight.assert_not_called()
    result = await machine._handle_get_session_artifacts(cmd.model_copy(update={"scan_history": True}))
    requested, sid = machine._history_page_singleflight.call_args.args
    assert sid == "session" and requested.limit == 12 and requested.detail == "summary"
    assert result.history_more and result.history_before == "older"
    assert len(result.entries) == 2
    assert isinstance(deserialize(serialize(result)), SessionArtifacts)
    assert len(transport.sent) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["full", "thumbnail"])
async def test_catalog_scope_mismatch_and_foreign_preview_are_rejected(tmp_path, variant):
    machine, _ = _mk_machine()
    a, b = _mk_ctx("a", "a"), _mk_ctx("b", "b")
    a.cwd = b.cwd = str(tmp_path)
    machine.sessions.update(a=a, b=b)
    service = machine._artifacts
    await service.uploads(a, "msg", [{"filename": "test.txt", "data": "c2VjcmV0"}])
    entry = service.store.page(service.context_scope(a))[0][0]
    machine._read_file_preview = lambda *args: pytest.fail("must not read a foreign file")
    result = await machine._handle_get_session_artifact_preview(GetSessionArtifactPreview(
        sid="b", engine="claude", artifact_id=entry["id"], request_id="x", client_id="client", variant=variant))
    assert result.error and result.sid == "b"
    for field, value in (("engine", "codex"), ("space", "work")):
        args = dict(sid="a", engine="claude", request_id="x") | {field: value}
        result = await machine._handle_get_session_artifacts(GetSessionArtifacts(**args))
        assert result.error and not result.entries


@pytest.mark.asyncio
async def test_uploaded_file_previews_without_native_session_call(tmp_path):
    machine, _ = _mk_machine()
    ctx = _mk_ctx("session", "session")
    ctx.cwd = str(tmp_path)
    machine.sessions[ctx.key] = ctx
    service = machine._artifacts
    await service.uploads(ctx, "msg", [{"filename": "note.txt", "data": "aGVsbG8="}])
    entry = service.store.page(service.context_scope(ctx))[0][0]
    result = await machine._handle_get_session_artifact_preview(GetSessionArtifactPreview(
        sid="session", engine="claude", artifact_id=entry["id"], request_id="x", client_id="client"))
    assert result.content == "hello" and not result.writable and not result.error


def test_artifact_protocol_bounds():
    cmd = GetSessionArtifacts(sid="a", engine="codex", request_id="r", before=4)
    assert deserialize(serialize(cmd)) == cmd
    with pytest.raises(ValueError):
        GetSessionArtifacts(engine="codex", request_id="r", before=-1)
    thumbnail = GetSessionArtifactPreview(sid="a", engine="codex", request_id="r",
        artifact_id="image", variant="thumbnail")
    assert deserialize(serialize(thumbnail)) == thumbnail
    with pytest.raises(ValueError):
        GetSessionArtifactPreview(engine="codex", request_id="r", artifact_id="image", variant="invalid")


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["full", "thumbnail"])
async def test_historical_images_preview_only_to_the_requester(tmp_path, variant):
    machine, transport = _mk_machine()
    ctx = _mk_ctx("session", "session")
    ctx.cwd = str(tmp_path)
    machine.sessions[ctx.key] = ctx
    service = machine._artifacts
    scope = service.context_scope(ctx)
    service.store.text(scope, "[image](output.png)", ctx.cwd)
    service.store.history(scope, History(session_id="session", revision="rev", turns=[{
        "id": "turn", "ts": 1000, "files": [{"filename": "old.txt"}],
        "blocks": [{"kind": "process", "tool": "image_generation", "status": "succeeded",
                    "input": {"file_path": str(tmp_path / "output.png"),
                              "history_image": {"image_id": "generated"}}}],
    }]), ctx.cwd)
    rows = service.store.page(scope)[0]
    assert len(rows) == 2  # Native image locator upgrades the same output file.
    image = next(row for row in rows if row["image_id"])
    missing = next(row for row in rows if row["identity"].startswith("missing:"))
    assert not public_entry(missing)["available"]
    machine._read_file_preview = lambda *args: pytest.fail("must use the source-bound historical locator")
    machine._handle_get_history_image = AsyncMock(return_value=HistoryImage(
        session_id="session", turn_id="turn", image_id="generated", variant="full",
        request_id="read", revision="rev", media_type="image/png", data=base64.b64encode(_test_image()).decode()))
    response = await service.preview(GetSessionArtifactPreview(
        sid="session", engine="claude", artifact_id=image["id"], request_id="read", client_id="reader", variant=variant))
    assert response.to == "reader" and response.data and not response.error
    assert machine._handle_get_history_image.call_args.args[0].variant == variant
    assert machine._handle_get_history_image.call_args.kwargs == {"publish": False}
    assert len(transport.sent) == 1 and transport.sent[0].type == "file_preview"
    response = await service.preview(GetSessionArtifactPreview(
        sid="session", engine="claude", artifact_id=missing["id"], request_id="missing", client_id="reader"))
    assert response.error and not response.data


def _test_image():
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (640, 320), "#b4c8ed").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["upload", "delivered"])
async def test_thumbnail_is_small_readonly_and_never_opens_full_preview(tmp_path, source):
    from PIL import Image
    machine, transport = _mk_machine()
    ctx = _mk_ctx("session", "session")
    ctx.cwd = str(tmp_path)
    machine.sessions[ctx.key] = ctx
    service = machine._artifacts
    raw = _test_image()
    if source == "upload":
        await service.uploads(ctx, "msg", [{"filename": "image.png", "data": base64.b64encode(raw).decode()}])
    else:
        (tmp_path / "image.png").write_bytes(raw)
        service.store.text(service.context_scope(ctx), "[picture](image.png)", ctx.cwd)
    row = service.store.page(service.context_scope(ctx))[0][0]
    machine._handle_get_file_preview = AsyncMock(side_effect=AssertionError("must not open full preview"))
    response = await service.preview(GetSessionArtifactPreview(
        sid="session", engine="claude", artifact_id=row["id"], request_id="thumb",
        client_id="reader", variant="thumbnail"))
    assert not response.error and response.media_type == "image/webp" and not response.writable
    assert response.to == "reader" and response.sid == "session"
    decoded = base64.b64decode(response.data)
    assert response.size == len(decoded) < 64 * 1024
    with Image.open(io.BytesIO(decoded)) as thumbnail:
        assert thumbnail.size == (160, 80)
    assert len(transport.sent) == 1
    machine._handle_get_file_preview.assert_not_called()


@pytest.mark.asyncio
async def test_automatic_thumbnail_does_not_grant_external_file_access(tmp_path):
    machine, _ = _mk_machine()
    ctx = _mk_ctx("session", "session")
    cwd = tmp_path / "project"
    cwd.mkdir()
    ctx.cwd = str(cwd)
    machine.sessions[ctx.key] = ctx
    service = machine._artifacts
    outside = tmp_path / "private.png"
    outside.write_bytes(_test_image())
    service.store.text(service.context_scope(ctx), f"[image]({outside})", ctx.cwd)
    row = service.store.page(service.context_scope(ctx))[0][0]
    cmd = GetSessionArtifactPreview(sid="session", engine="claude", artifact_id=row["id"],
        request_id="thumb", client_id="reader", variant="thumbnail")
    response = await service.preview(cmd)
    assert response.error and not response.data
    assert not machine._preview_capability_store.snapshot("claude", "code", "session")
    # A grant for another session is not enough, even with the same cwd.
    machine._preview_capability_store.grant_path("claude", "code", "other", str(outside),
        mode="read", source="user_approved")
    assert (await service.preview(cmd)).error
    machine._preview_capability_store.grant_path("claude", "code", "session", str(outside),
        mode="read", source="user_approved")
    assert not (await service.preview(cmd)).error
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(_test_image())
    replacement.replace(outside)
    assert (await service.preview(cmd)).error


@pytest.mark.asyncio
async def test_thumbnail_rejects_oversized_files_and_non_images(tmp_path):
    machine, _ = _mk_machine()
    ctx = _mk_ctx("session", "session")
    ctx.cwd = str(tmp_path)
    machine.sessions[ctx.key] = ctx
    for name in ["large.png", "script.svg", "notes.md"]:
        target = tmp_path / name
        target.write_text("not an image")
        if name == "large.png":
            with target.open("ab") as stream:
                stream.truncate(7 * 1024 * 1024)
        machine._artifacts.store.text(machine._artifacts.context_scope(ctx), f"[file]({name})", ctx.cwd)
    for row in machine._artifacts.store.page(machine._artifacts.context_scope(ctx))[0]:
        result = await machine._artifacts.preview(GetSessionArtifactPreview(
            sid="session", engine="claude", artifact_id=row["id"], request_id="thumb", variant="thumbnail"))
        assert result.error and not result.data


def test_thumbnail_pixel_limit_is_checked_before_decode(monkeypatch):
    monkeypatch.setattr("cc_remote.wrapper.session_artifacts.MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="尺寸过大"):
        image_thumbnail(_test_image())


@pytest.mark.asyncio
async def test_artifact_store_failure_does_not_block_native_rekey_or_delete():
    machine, _ = _mk_machine()
    ctx = _mk_ctx("tmp-one")
    class BrokenStore:
        def move(self, *args):
            raise OSError("unavailable sidecar")

        def drop_session(self, *args):
            raise OSError("unavailable sidecar")

    machine._artifacts._store = BrokenStore()
    await machine._rekey_preview_session(ctx, "tmp-one", "native-one")
    await machine._drop_preview_session(ctx.engine, "native-one")


def test_account_device_and_space_are_part_of_artifact_scope():
    machine, _ = _mk_machine()
    scope = machine._artifacts.scope
    assert scope("codex", "code", "primary:sid") != scope("codex", "code", "secondary:sid")
    assert scope("claude", "code", "sid") != scope("claude", "work", "sid")
    assert json.loads(scope("codex", "code", "primary:sid"))[0] == machine.cfg.machine_id


@pytest.mark.asyncio
async def test_readonly_btw_artifacts_keep_owner_isolation(tmp_path):
    from tests.test_btw_lifecycle import _fork

    machine, _ = _mk_machine()
    ctx = _fork(machine)
    ctx.cwd = str(tmp_path)
    await machine._artifacts.uploads(ctx, "upload", [{"filename": "retained.txt", "data": "aGk="}])
    result = await machine._handle(GetSessionArtifacts(
        sid=ctx.key, engine="codex", request_id="list", client_id="tab", owner_id="owner"))
    assert len(result.entries) == 1 and ctx.btw_destroyed
    preview = GetSessionArtifactPreview(
        sid=ctx.key, engine="codex", artifact_id=result.entries[0].id,
        request_id="preview", client_id="tab", owner_id="owner")
    response = await machine._handle(preview)
    assert response.content == "hi" and not response.writable
    rejected = await machine._handle(preview.model_copy(update={"owner_id": "another-owner"}))
    assert rejected.type == "error"
    ctx.sdk.force_reconnect.assert_not_called()
    ctx.sdk.disconnect.assert_not_called()


@pytest.mark.asyncio
async def test_claude_steering_upload_outlives_its_temporary_prompt_files(tmp_path):
    from types import SimpleNamespace
    from cc_remote.protocol import Steer
    from cc_remote.wrapper import claude_steer

    machine, _ = _mk_machine()
    ctx = _mk_ctx("session", "session")
    ctx.cwd, ctx.state, ctx.write_state = str(tmp_path), "running", "writable"
    ctx.sdk = SimpleNamespace(steer=AsyncMock())
    reject = AsyncMock()
    await claude_steer.handle(machine, ctx, Steer(
        sid="session", msg_id="guide", cmd_id="command", client_id="browser", prompt="read this",
        files=[{"filename": "notes.txt", "data": "aGk="}]), reject)
    reject.assert_not_called()
    ctx.sdk.steer.assert_awaited_once()
    staged = ctx.claude_steer_attachment_dirs[:]
    assert staged
    claude_steer.cleanup(ctx)
    assert all(not Path(path).exists() for path in staged)
    row = machine._artifacts.store.page(machine._artifacts.context_scope(ctx))[0][0]
    assert row["name"] == "notes.txt" and Path(row["path"]).read_text() == "hi"
