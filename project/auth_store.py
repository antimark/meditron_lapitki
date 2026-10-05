from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

USERNAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_.-]{3,64}$")


class UserStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")

    @staticmethod
    def normalize_username(username: str) -> str:
        return (username or "").strip()

    @staticmethod
    def validate_username(username: str) -> bool:
        return bool(USERNAME_RE.fullmatch(username or ""))

    def create_user(self, username: str, password: str) -> tuple[bool, str]:
        username = self.normalize_username(username)
        if not self.validate_username(username):
            return False, "Логин должен содержать 3–64 символа: буквы, цифры, _, . или -."
        if len(password or "") < 8:
            return False, "Пароль должен содержать минимум 8 символов."

        password_hash = generate_password_hash(password, method="scrypt")
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, password_hash, created_at),
                )
            return True, ""
        except sqlite3.IntegrityError:
            return False, "Пользователь с таким логином уже существует."

    def verify_user(self, username: str, password: str) -> bool:
        username = self.normalize_username(username)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None:
            return False
        return check_password_hash(row["password_hash"], password or "")

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"] if row else 0)
