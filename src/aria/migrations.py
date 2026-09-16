"""Additive Phase 2 migration; legacy learning evidence is never relabeled explicit."""

def migrate_v2(connection):
    connection.executescript("""
        BEGIN;
        ALTER TABLE learning_events ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}';
        CREATE TABLE explicit_memories (
            id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL REFERENCES profiles(student_id) ON DELETE CASCADE,
            key TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        );
        CREATE INDEX explicit_student ON explicit_memories(student_id, occurred_at);
        CREATE TABLE course_records (
            course_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(course_id, kind, id)
        );
        PRAGMA user_version = 2;
        COMMIT;
    """)
