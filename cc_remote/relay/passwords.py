"""Persistent password overrides in the relay's private device database."""
from contextlib import closing
import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3

from cc_remote.config import RelayConfig, parse_login_users
from cc_remote.relay.auth import verify_password


class PasswordStore:
    def __init__(self, path: Path, cfg: RelayConfig) -> None:
        self.path = path
        users = parse_login_users(cfg.login_users_json)
        self.configured = {name: entry[0] for name, entry in users.items()} or {"": cfg.login_password}
        # Bind overrides to the configured account and password. An operator
        # can reset the credential by changing its configured value and restarting.
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS login_password_overrides (
                subject TEXT PRIMARY KEY,
                salt BLOB NOT NULL,
                configured_hash BLOB NOT NULL,
                password_hash BLOB NOT NULL
            )""")
            stale = [(name,) for name, salt, expected in db.execute(
                "SELECT subject, salt, configured_hash FROM login_password_overrides"
            ) if name not in self.configured or not hmac.compare_digest(
                self._hash(self.configured[name], salt + b"configured"), expected)]
            db.executemany("DELETE FROM login_password_overrides WHERE subject = ?", stale)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=0.5)

    @staticmethod
    def _hash(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)

    def verify(self, subject: str | None, candidate: str, configured: str) -> bool:
        key = subject or ""
        with closing(self._connect()) as db:
            override = db.execute(
                "SELECT salt, password_hash FROM login_password_overrides WHERE subject = ?",
                (key,),
            ).fetchone()
        if override is None:
            return verify_password(candidate, configured)
        salt, expected = override
        return hmac.compare_digest(self._hash(candidate, salt), expected)

    def save(self, subject: str | None, salt: bytes, configured_hash: bytes, password_hash: bytes) -> None:
        key = subject or ""
        with closing(self._connect()) as db, db:
            db.execute("""INSERT INTO login_password_overrides(subject, salt, configured_hash, password_hash)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(subject) DO UPDATE SET salt = excluded.salt,
                    configured_hash = excluded.configured_hash, password_hash = excluded.password_hash""",
                       (key, salt, configured_hash, password_hash))

    def prepare(self, subject: str | None, password: str) -> tuple[bytes, bytes, bytes]:
        salt = secrets.token_bytes(16)
        configured_hash = self._hash(self.configured[subject or ""], salt + b"configured")
        return salt, configured_hash, self._hash(password, salt)
