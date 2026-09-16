"""Recurring weekly schedules using the system's local date and timezone by default."""
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, tzinfo
import json
from typing import Protocol

from aria.storage import Database
from aria.validation import aware, nonempty


@dataclass(frozen=True)
class WeeklyClass:
    id: str
    student_id: str
    course_id: str
    title: str
    weekday: int  # Monday=0, Sunday=6
    starts_at: time
    ends_at: time
    location: str = ""

    def validate(self) -> None:
        for key in ("id", "student_id", "course_id", "title"):
            nonempty(getattr(self, key), key)
        if type(self.weekday) is not int or not 0 <= self.weekday <= 6:
            raise ValueError("weekday must be an integer from 0 (Monday) to 6 (Sunday)")
        if not isinstance(self.location, str):
            raise ValueError("location must be a string")
        for value in (self.starts_at, self.ends_at):
            if not isinstance(value, time) or value.tzinfo is not None:
                raise ValueError("class times must be naive local wall times")
        if self.ends_at <= self.starts_at:
            raise ValueError("classes must end after they start on the same day")


@dataclass(frozen=True)
class ClassOccurrence:
    weekly_class: WeeklyClass
    starts_at: datetime
    ends_at: datetime


class ScheduleRepository(Protocol):
    def save(self, entry: WeeklyClass) -> None: ...
    def list(self, student_id: str) -> list[WeeklyClass]: ...
    def delete(self, student_id: str, class_id: str) -> None: ...


class SQLiteScheduleRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, entry: WeeklyClass) -> None:
        entry.validate()
        self.db.require_student(entry.student_id)
        payload = asdict(entry)
        payload.update(starts_at=entry.starts_at.isoformat(), ends_at=entry.ends_at.isoformat())
        with self.db.connection:
            existing = self.db.connection.execute("SELECT student_id FROM classes WHERE id = ?", (entry.id,)).fetchone()
            if existing and existing["student_id"] != entry.student_id:
                raise ValueError("Class ID belongs to another student")
            self.db.connection.execute(
                "INSERT INTO classes VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (entry.id, entry.student_id, json.dumps(payload)),
            )

    def list(self, student_id: str) -> list[WeeklyClass]:
        self.db.require_student(student_id)
        result = []
        for row in self.db.connection.execute("SELECT payload FROM classes WHERE student_id = ?", (student_id,)):
            data = json.loads(row["payload"])
            data.update(starts_at=time.fromisoformat(data["starts_at"]), ends_at=time.fromisoformat(data["ends_at"]))
            result.append(WeeklyClass(**data))
        return sorted(result, key=lambda c: (c.weekday, c.starts_at, c.id))

    def delete(self, student_id: str, class_id: str) -> None:
        self.db.require_student(student_id)
        with self.db.connection:
            cursor = self.db.connection.execute("DELETE FROM classes WHERE id = ? AND student_id = ?", (class_id, student_id))
            if not cursor.rowcount:
                raise KeyError(class_id)


class Timetable:
    def __init__(self, repository: ScheduleRepository, *,
                 clock: Callable[[], datetime] | None = None, zone: tzinfo | None = None) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.zone = zone

    def _now(self) -> datetime:
        now = self.clock()
        aware(now)
        return now.astimezone(self.zone)

    def classes_on(self, student_id: str, day: date) -> list[ClassOccurrence]:
        def local(value: time) -> datetime:
            wall = datetime.combine(day, value)
            # No fixed offset cached: system conversion uses DST rules for this date.
            return wall.replace(tzinfo=self.zone) if self.zone else wall.astimezone()
        return [ClassOccurrence(c, local(c.starts_at), local(c.ends_at))
                for c in self.repository.list(student_id) if c.weekday == day.weekday()]

    def get_todays_classes(self, student_id: str) -> list[ClassOccurrence]:
        return self.classes_on(student_id, self._now().date())

    def get_tomorrows_classes(self, student_id: str) -> list[ClassOccurrence]:
        return self.classes_on(student_id, self._now().date() + timedelta(days=1))

    def get_next_class(self, student_id: str) -> ClassOccurrence | None:
        """First class starting at or after now; excludes already ongoing classes."""
        now = self._now()
        for offset in range(8):  # include next week's same weekday
            for occurrence in self.classes_on(student_id, now.date() + timedelta(days=offset)):
                if occurrence.starts_at.timestamp() >= now.timestamp():
                    return occurrence
        return None
