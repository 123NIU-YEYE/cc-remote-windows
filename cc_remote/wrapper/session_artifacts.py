"""Session-owned attachment catalog. No directory crawl or model/engine calls.

Uploads are immutable private copies; delivered files remain references to the
user's working files. Historical discovery consumes bounded summary pages.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import threading
import time
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import unquote, urlsplit

from cc_remote.attachments import MAX_IMAGE_PIXELS, decode_attachment, validate_attachments


def image_thumbnail(data: bytes) -> bytes:
    """A static, metadata-free list thumbnail; never send original image bytes."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > MAX_IMAGE_PIXELS:
            raise ValueError("图片尺寸过大")
        # Use only the first frame of animated images, and honor phone orientation.
        source.thumbnail((160, 160), Image.Resampling.LANCZOS)
        image = ImageOps.exif_transpose(source).convert("RGBA")
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=75, method=4)
        result = output.getvalue()
        if len(result) > 64 * 1024:
            raise ValueError("缩略图尺寸过大")
        return result


def local_links(text: str, cwd: str) -> list[str]:
    """Only explicit Markdown destinations / visualize outputs, not tool reads."""
    text = re.sub(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", "", text, flags=re.S)
    candidates = []
    for match in re.finditer(r"\]\(\s*(?:<([^>\n]+)>|([^\s]+?))(?:\s+\"[^\"]*\")?\s*\)", text):
        candidates.append(match.group(1) or match.group(2))
    for match in re.finditer(r'visualize(\{[^\n]*?\})', text):
        try:
            candidates.append(json.loads(match.group(1)).get("path"))
        except (ValueError, TypeError):
            pass
    paths = []
    for value in candidates[:128]:
        if not isinstance(value, str) or len(value) > 4096:
            continue
        try:
            parsed = urlsplit(value)
        except ValueError:
            continue
        if parsed.scheme not in ("", "file") or parsed.netloc or value.startswith("#"):
            continue
        path = re.sub(r":\d+(?::\d+)?$", "", unquote(parsed.path))
        if not path or not Path(path).suffix:
            continue
        path = os.path.abspath(os.path.join(cwd, os.path.expanduser(path)))
        if path not in paths:
            paths.append(path)
    return paths


def artifact_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif"}:
        return "image"
    if ext in {".md", ".txt", ".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".csv"}:
        return "document"
    return "file"


class SessionArtifactStore:
    def __init__(self, state_dir: Path):
        self.root = Path(state_dir) / "session-artifacts"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise OSError("unsafe artifact directory")
        os.chmod(self.root, 0o700)
        self.path = self.root / "index.sqlite3"
        if self.path.is_symlink():
            raise OSError("unsafe artifact database")
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                    id TEXT NOT NULL, identity TEXT NOT NULL, name TEXT NOT NULL,
                    path TEXT NOT NULL, source TEXT NOT NULL, created_at REAL NOT NULL,
                    turn_id TEXT, image_id TEXT, UNIQUE(scope, identity), UNIQUE(scope, id)
                );
                CREATE TABLE IF NOT EXISTS scope_aliases (old TEXT PRIMARY KEY, current TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS artifact_page ON artifacts(scope, created_at DESC, seq DESC);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def canonical_scope(db, scope):
        for _ in range(32):
            row = db.execute("SELECT current FROM scope_aliases WHERE old=?", (scope,)).fetchone()
            if row is None:
                return scope
            scope = row[0]
        raise ValueError("artifact scope alias cycle")

    def add(self, scope, identity, name, path, source, *, created_at=None,
            turn_id=None, image_id=None):
        identifier = hashlib.sha256(f"{scope}\0{identity}".encode()).hexdigest()
        with self.lock, self.connect() as db:
            scope = self.canonical_scope(db, scope)
            db.execute("""INSERT INTO artifacts
                (scope,id,identity,name,path,source,created_at,turn_id,image_id)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(scope,identity) DO UPDATE SET
                image_id=COALESCE(excluded.image_id, artifacts.image_id),
                turn_id=COALESCE(excluded.turn_id, artifacts.turn_id),
                path=CASE WHEN excluded.image_id IS NOT NULL THEN excluded.path ELSE artifacts.path END""",
                (scope, identifier, identity, name[:1024], path, source,
                 created_at if created_at is not None else time.time(), turn_id, image_id))

    def uploads(self, scope, msg_id, files=None, images=None):
        if validate_attachments(images, files):
            raise ValueError("invalid artifact upload")
        items = [(f.get("filename") or "attachment", f["data"]) for f in files or []]
        extensions = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}
        items.extend((f"image-{i:02d}" + extensions.get(f["media_type"], ".png"), f["data"])
                     for i, f in enumerate(images or []))
        for i, (name, encoded) in enumerate(items):
            identity = f"upload:{msg_id}:{i}:{name}"
            safe = re.sub(r"[^\w. -]", "_", Path(name).name).strip(". ")[:160] or "attachment"
            data = decode_attachment(encoded)
            with self.lock:
                with self.connect() as db:
                    scope = self.canonical_scope(db, scope)
                    previous = db.execute("SELECT path FROM artifacts WHERE scope=? AND identity=?",
                                          (scope, identity)).fetchone()
                digest = hashlib.sha256(f"{scope}\0{identity}".encode()).hexdigest()
                path = Path(previous[0]) if previous else self.root / f"{digest}-{safe}"
                if path.parent != self.root:
                    raise OSError("unsafe artifact upload path")
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                except FileExistsError:
                    info = path.lstat()
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_uid != os.geteuid()):
                        raise OSError("unsafe artifact upload")
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd, "rb") as existing:
                        if existing.read(len(data) + 1) != data:
                            raise OSError("artifact upload content changed")
                else:
                    try:
                        with os.fdopen(fd, "wb") as out:
                            out.write(data)
                            out.flush()
                            os.fsync(out.fileno())
                    except BaseException:
                        path.unlink(missing_ok=True)
                        raise
                self.add(scope, identity, name, str(path), "upload", turn_id=msg_id)

    def text(self, scope, text, cwd, *, created_at=None):
        for path in local_links(text, cwd):
            self.add(scope, "path:" + path, Path(path).name, path, "delivered", created_at=created_at)

    def history(self, scope, history, cwd):
        for turn in history.turns:
            stamp = turn.ts / 1000 if turn.ts else None
            for block in turn.blocks:
                if block.get("kind") == "text":
                    self.text(scope, block.get("text", ""), cwd, created_at=stamp)
                elif block.get("kind") == "process" and block.get("tool") == "image_generation" and block.get("status") == "succeeded":
                    item = block.get("input") or {}
                    ref = item.get("history_image") or {}
                    path = item.get("file_path")
                    if isinstance(ref.get("image_id"), str):
                        image_id = ref["image_id"]
                        self.add(scope, "path:" + os.path.abspath(os.path.join(cwd, path)) if path else f"generated:{turn.id}:{image_id}",
                                 Path(path).name if path else "generated.png",
                                 f"artifacts/{image_id}.png", "delivered", created_at=stamp,
                                 turn_id=turn.id, image_id=image_id)
                    elif isinstance(path, str):
                        self.text(scope, f"[image](<{path}>)", cwd, created_at=stamp)
            uploaded = any(self.has_uploads(scope, key) for key in (turn.id, turn.clientMsgId) if key)
            if not uploaded:
                for i, file in enumerate(turn.files or []):
                    name = file.get("filename", "attachment")
                    identity = f"missing:{turn.id}:{i}:{name}"
                    self.add(scope, identity, name, f"artifacts/unavailable/{i}/{name}",
                             "upload", created_at=stamp, turn_id=turn.id)
            for i, ref in enumerate([] if uploaded else turn.imageRefs or []):
                data = ref.model_dump() if hasattr(ref, "model_dump") else ref
                image_id = data.get("image_id")
                if not image_id:
                    continue
                identity = f"history:{turn.id}:{image_id}"
                self.add(scope, identity, f"image-{i:02d}.png", f"artifacts/{image_id}.png",
                         "upload", created_at=stamp, turn_id=turn.id, image_id=image_id)

    def page(self, scope, before=None, limit=40):
        with self.lock, self.connect() as db:
            scope = self.canonical_scope(db, scope)
            anchor = db.execute("SELECT created_at, seq FROM artifacts WHERE scope=? AND seq=?",
                                (scope, before)).fetchone() if before else None
            if before and anchor is None:
                raise ValueError("invalid catalog cursor")
            rows = db.execute("""SELECT * FROM artifacts WHERE scope=? AND
                (? IS NULL OR created_at < ? OR (created_at = ? AND seq < ?))
                ORDER BY created_at DESC,seq DESC LIMIT ?""",
                (scope, before, anchor[0] if anchor else None, anchor[0] if anchor else None,
                 before, limit + 1)).fetchall()
        page = rows[:limit]
        return [dict(row) for row in page], page[-1]["seq"] if page else before, len(rows) > limit

    def get(self, scope, identifier):
        with self.lock, self.connect() as db:
            scope = self.canonical_scope(db, scope)
            row = db.execute("SELECT * FROM artifacts WHERE scope=? AND id=?",
                             (scope, identifier)).fetchone()
        return dict(row) if row else None

    def has_uploads(self, scope, msg_id):
        with self.lock, self.connect() as db:
            scope = self.canonical_scope(db, scope)
            return bool(db.execute("SELECT 1 FROM artifacts WHERE scope=? AND turn_id=? AND image_id IS NULL AND identity NOT LIKE 'missing:%' LIMIT 1",
                                   (scope, msg_id)).fetchone())

    def drop_session(self, machine_id, engine, sid):
        with self.lock, self.connect() as db:
            scopes = [row[0] for row in db.execute("SELECT DISTINCT scope FROM artifacts")]
            for scope in scopes:
                parts = json.loads(scope)
                if parts[0] != machine_id or parts[1] != engine or parts[3] != sid:
                    continue
                for row in db.execute("SELECT path FROM artifacts WHERE scope=? AND source='upload' AND image_id IS NULL", (scope,)):
                    path = Path(row[0])
                    if path.parent == self.root:
                        path.unlink(missing_ok=True)
                db.execute("DELETE FROM artifacts WHERE scope=?", (scope,))

    def move(self, old_scope, new_scope):
        with self.lock, self.connect() as db:
            old_scope = self.canonical_scope(db, old_scope)
            new_scope = self.canonical_scope(db, new_scope)
            if old_scope == new_scope:
                return
            db.execute("INSERT OR REPLACE INTO scope_aliases VALUES (?,?)", (old_scope, new_scope))
            db.execute("UPDATE OR IGNORE artifacts SET scope=? WHERE scope=?", (new_scope, old_scope))


def public_entry(row):
    size = 0
    available = bool(row["image_id"])
    if not available and not row["identity"].startswith("missing:"):
        try:
            info = os.lstat(row["path"])
            available = stat.S_ISREG(info.st_mode)
            size = info.st_size if available else 0
        except OSError:
            pass
    return {key: row[key] for key in ("id", "name", "path", "source", "created_at")} | {
        "kind": artifact_kind(row["name"]), "available": available, "size": size,
    }
