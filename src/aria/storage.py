"""SQLite ownership and versioned schema shared by replaceable repositories."""
from pathlib import Path
import sqlite3


class Database:
    """Single-threaded connection. Use one instance per worker; close when done.

    Local paths only. SQLite transactions make individual repository writes atomic.
    """

    def __init__(self, path: str | Path = "data/aria.sqlite3") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.close()
            raise ValueError(f"Unsupported database schema version: {version}")
        if version == 0:
            self.connection.executescript("""
                BEGIN;
                CREATE TABLE profiles (
                    student_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL REFERENCES profiles(student_id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX conversations_student ON conversations(student_id, occurred_at);
                CREATE TABLE learning_events (
                    id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL REFERENCES profiles(student_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, course_id TEXT NOT NULL, topic TEXT NOT NULL,
                    details TEXT NOT NULL, occurred_at TEXT NOT NULL
                );
                CREATE INDEX events_student ON learning_events(student_id, occurred_at);
                CREATE TABLE classes (
                    id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL REFERENCES profiles(student_id) ON DELETE CASCADE,
                    payload TEXT NOT NULL
                );
                CREATE INDEX classes_student ON classes(student_id);
                PRAGMA user_version = 1;
                COMMIT;
            """)

    def require_student(self, student_id: str) -> None:
        if not self.connection.execute(
            "SELECT 1 FROM profiles WHERE student_id = ?", (student_id,)
        ).fetchone():
            raise KeyError(f"Unknown student: {student_id}")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
