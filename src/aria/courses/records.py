"""Local metadata/provenance persistence only, not ingestion or content retrieval."""
from dataclasses import asdict, dataclass, field
import json
from typing import Protocol
from aria.validation import nonempty


@dataclass(frozen=True)
class CourseDocument:
    id: str
    course_id: str
    title: str
    local_path: str
    media_type: str
    content_hash: str = ""


@dataclass(frozen=True)
class SourceReference:
    id: str
    course_id: str
    document_id: str
    page: int | None = None
    slide: int | None = None
    section: str | None = None
    source_text: str | None = None
    source_image: str | None = None  # local image reference; never fetched/rendered here


@dataclass(frozen=True)
class Topic:
    id: str
    course_id: str
    title: str
    source_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Concept:
    id: str
    course_id: str
    topic_id: str
    title: str
    source_ids: list[str]
    prerequisites: list[str] = field(default_factory=list)
    related_concepts: list[str] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)  # source-backed concept IDs


class CourseRepository(Protocol):
    def save(self, record) -> None: ...
    def get(self, course_id: str, kind: str, record_id: str): ...
    def list(self, course_id: str, kind: str) -> list: ...


class SQLiteCourseRepository:
    def __init__(self, db):
        from aria.courses import Course
        self.db = db
        self.types = {c.__name__: c for c in (Course, CourseDocument, SourceReference, Topic, Concept)}

    def get(self, course_id, kind, record_id):
        if kind not in self.types:
            raise ValueError("Unknown course record kind")
        row = self.db.connection.execute(
            "SELECT payload FROM course_records WHERE course_id = ? AND kind = ? AND id = ?",
            (course_id, kind, record_id)).fetchone()
        if row is None:
            raise KeyError((course_id, kind, record_id))
        return self.types[kind](**json.loads(row["payload"]))

    def list(self, course_id, kind):
        if kind not in self.types:
            raise ValueError("Unknown course record kind")
        return [self.types[kind](**json.loads(r["payload"])) for r in self.db.connection.execute(
            "SELECT payload FROM course_records WHERE course_id = ? AND kind = ? ORDER BY id", (course_id, kind))]

    def save(self, record):
        from aria.courses import Course
        if type(record).__name__ not in self.types or self.types[type(record).__name__] is not type(record):
            raise ValueError("Unsupported course record")
        nonempty(record.id, "id")
        course_id = record.id if isinstance(record, Course) else record.course_id
        nonempty(course_id, "course_id")
        if hasattr(record, "title"):
            nonempty(record.title, "title")
        with self.db.connection:
            self.db.connection.execute("BEGIN IMMEDIATE")
            if not isinstance(record, Course):
                self.get(course_id, "Course", course_id)
            if isinstance(record, (CourseDocument, SourceReference)):
                try:
                    previous = self.get(course_id, type(record).__name__, record.id)
                except KeyError:
                    previous = None
                if previous is not None and previous != record:
                    raise ValueError("Document/source records are immutable; use a new revision ID")
            if isinstance(record, CourseDocument):
                nonempty(record.local_path, "local_path")
                nonempty(record.media_type, "media_type")
                if "://" in record.local_path:
                    raise ValueError("Document paths must be local")
                if not isinstance(record.content_hash, str):
                    raise ValueError("content_hash must be a string")
            if isinstance(record, SourceReference):
                self.get(course_id, "CourseDocument", record.document_id)
                for value in (record.page, record.slide):
                    if value is not None and (type(value) is not int or value < 1):
                        raise ValueError("Page/slide must be a positive integer")
                for value in (record.section, record.source_text, record.source_image):
                    if value is not None:
                        nonempty(value, "source value")
                if record.source_image and "://" in record.source_image:
                    raise ValueError("Source images must be local references")
                if all(v is None for v in (record.page, record.slide, record.section, record.source_text, record.source_image)):
                    raise ValueError("A source needs a locator or an excerpt")
            for attribute in ("source_ids", "syllabus_source_ids"):
                if hasattr(record, attribute):
                    self._references(course_id, "SourceReference", getattr(record, attribute))
            if isinstance(record, Concept):
                self.get(course_id, "Topic", record.topic_id)
                if not record.source_ids:
                    raise ValueError("Concepts require provenance")
                for attribute in ("prerequisites", "related_concepts", "misconceptions"):
                    refs = getattr(record, attribute)
                    if record.id in refs:
                        raise ValueError("A concept cannot link to itself")
                    self._references(course_id, "Concept", refs)
            self.db.connection.execute(
                "INSERT INTO course_records VALUES (?, ?, ?, ?) ON CONFLICT(course_id, kind, id) DO UPDATE SET payload=excluded.payload",
                (course_id, type(record).__name__, record.id, json.dumps(asdict(record))))

    def _references(self, course_id, kind, values):
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError("References must be non-empty string IDs in a list")
        if len(set(values)) != len(values):
            raise ValueError("Duplicate references")
        for value in values:
            self.get(course_id, kind, value)
