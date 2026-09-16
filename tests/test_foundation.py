import json
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from aria.agent import Tool, ToolAgent
from aria.app import Aria
from aria.storage import Database
from aria.student_memory import EventKind, StudentProfile
from aria.timetable import Timetable, WeeklyClass

UTC = timezone.utc


class FoundationTest(unittest.TestCase):
    def setUp(self):
        self.app = Aria(":memory:")
        self.addCleanup(self.app.close)
        self.app.profiles.save(StudentProfile("s1", "Student One"))
        self.app.profiles.save(StudentProfile("s2", "Student Two"))

    def entry(self, **changes):
        return replace(WeeklyClass("c1", "s1", "course-1", "User class", 0, time(9), time(10)), **changes)

    def timetable(self, now, zone=UTC):
        return Timetable(self.app.schedule, clock=lambda: now, zone=zone)

    def test_profile_roundtrip_update_and_display_name(self):
        profile = StudentProfile("s1", "Student One", "One", ["course-1"], ["diagrams"], ["reading"], ["recall"])
        self.app.profiles.save(profile)
        self.assertEqual(profile, self.app.profiles.get("s1"))
        self.assertEqual("One", profile.display_name)
        profile.preferred_name = " "
        self.assertEqual("Student One", profile.display_name)
        self.app.profiles.save(profile)
        self.assertEqual(" ", self.app.profiles.get("s1").preferred_name)

    def test_profile_validation(self):
        for changes in ({"name": " "}, {"student_id": ""}, {"courses": ["x", "x"]},
                        {"strengths": [3]}, {"weaknesses": "text"}, {"preferred_name": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.app.profiles.save(replace(StudentProfile("x", "Name"), **changes))

    def test_profile_lists_independent(self):
        first, second = StudentProfile("a", "A"), StudentProfile("b", "B")
        first.courses.append("x")
        self.assertEqual([], second.courses)
        loaded = self.app.profiles.get("s1")
        loaded.courses.append("not saved")
        self.assertEqual([], self.app.profiles.get("s1").courses)

    def test_missing_students(self):
        for operation in (self.app.profiles.get, self.app.memory.history,
                          self.app.memory.conversations, self.app.schedule.list, self.app.agent_for):
            with self.assertRaises(KeyError):
                operation("missing")
        with self.assertRaises(KeyError):
            self.app.memory.record_mastery("missing", "c", "t")

    def test_conversations_and_isolation(self):
        msg = self.app.memory.record_message("s1", "session-a", "user", "Hello")
        self.app.memory.record_message("s1", "session-b", "assistant", "Welcome")
        self.assertEqual([msg], self.app.memory.conversations("s1", "session-a"))
        self.assertEqual(2, len(self.app.memory.conversations("s1")))
        self.assertEqual([], self.app.memory.conversations("s2"))

    def test_memory_validation(self):
        for role, content in (("invalid", "text"), ("user", "")):
            with self.assertRaises(ValueError):
                self.app.memory.record_message("s1", "session", role, content)
        with self.assertRaises(ValueError):
            self.app.memory.record_event("s1", "not-a-kind", "c", "t")
        with self.assertRaises(ValueError):
            self.app.memory.record_mastery("s1", "c", "")
        with self.assertRaises(ValueError):
            self.app.memory.record_study("s1", "c", "t", at=datetime(2026, 9, 16))

    def test_mastery_and_difficulties_derived_from_evidence(self):
        mem = self.app.memory
        mem.record_difficulty("s1", "c", "t")
        mem.record_mastery("s1", "c", "t")
        mem.record_study("s1", "c", "t")  # does not erase mastery
        mem.record_misconception("s1", "other", "t", "Needs review")
        self.assertEqual([("c", "t")], self.app.student_memory.topics_mastered("s1"))
        self.assertEqual([("other", "t")], self.app.student_memory.topics_struggled_with("s1"))
        self.assertEqual(4, len(mem.history("s1")))
        self.assertEqual([], mem.history("s2"))

    def test_history_orders_by_event_time_then_insertion(self):
        now = datetime(2026, 9, 16, tzinfo=UTC)
        mem = self.app.memory
        mem.record_mastery("s1", "c", "t", at=now)
        mem.record_difficulty("s1", "c", "t", at=now - timedelta(days=1))
        self.assertEqual([("c", "t")], self.app.student_memory.topics_mastered("s1"))
        mem.record_misconception("s1", "c", "t", "Review", at=now)
        self.assertEqual([], self.app.student_memory.topics_mastered("s1"))

    def test_utc_normalization(self):
        at = datetime(2026, 9, 16, 10, tzinfo=timezone(timedelta(hours=2)))
        self.app.memory.record_study("s1", "c", "t", at=at)
        self.assertEqual(datetime(2026, 9, 16, 8, tzinfo=UTC), self.app.memory.history("s1")[0].occurred_at)

    def test_schedule_validation(self):
        for changes in ({"weekday": -1}, {"weekday": 7}, {"weekday": True}, {"weekday": 2.5},
                        {"starts_at": time(10)}, {"ends_at": time(8)},
                        {"starts_at": time(9, tzinfo=UTC)}, {"title": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.app.schedule.save(self.entry(**changes))

    def test_today_tomorrow_sorted_and_week_rollover(self):
        self.app.schedule.save(self.entry())
        self.app.schedule.save(self.entry(id="earlier", starts_at=time(8), ends_at=time(9)))
        self.app.schedule.save(self.entry(id="sunday", weekday=6))
        tt = self.timetable(datetime(2026, 9, 20, 23, 59, tzinfo=UTC))
        self.assertEqual(["sunday"], [x.weekly_class.id for x in tt.get_todays_classes("s1")])
        self.assertEqual(["earlier", "c1"], [x.weekly_class.id for x in tt.get_tomorrows_classes("s1")])
        self.assertEqual(21, tt.get_next_class("s1").starts_at.day)

    def test_next_class_start_boundary_and_next_week(self):
        self.app.schedule.save(self.entry())
        start = datetime(2026, 9, 21, 9, tzinfo=UTC)
        self.assertEqual(start, self.timetable(start).get_next_class("s1").starts_at)
        self.assertEqual(start + timedelta(days=7), self.timetable(start + timedelta(seconds=1)).get_next_class("s1").starts_at)

    def test_empty_schedule_and_student_isolation(self):
        self.assertIsNone(self.app.timetable.get_next_class("s1"))
        self.assertEqual([], self.app.timetable.get_todays_classes("s1"))
        self.app.schedule.save(self.entry())
        self.assertIsNone(self.app.timetable.get_next_class("s2"))
        with self.assertRaises(ValueError):
            self.app.schedule.save(self.entry(student_id="s2"))
        with self.assertRaises(KeyError):
            self.app.schedule.delete("s2", "c1")

    def test_schedule_update_delete(self):
        self.app.schedule.save(self.entry())
        self.app.schedule.save(self.entry(title="Updated"))
        self.assertEqual([self.entry(title="Updated")], self.app.schedule.list("s1"))
        self.app.schedule.delete("s1", "c1")
        self.assertEqual([], self.app.schedule.list("s1"))

    def test_timezone_date_and_dst(self):
        self.app.schedule.save(self.entry(weekday=6))
        zone = ZoneInfo("America/New_York")
        tt = self.timetable(datetime(2026, 11, 2, 1, tzinfo=UTC), zone)
        today = tt.get_todays_classes("s1")[0]
        self.assertEqual(1, today.starts_at.day)  # still Sunday locally
        self.assertEqual(timedelta(hours=-5), today.starts_at.utcoffset())
        before = tt.classes_on("s1", datetime(2026, 10, 25).date())[0]
        self.assertEqual(timedelta(hours=-4), before.starts_at.utcoffset())

    def test_year_rollover(self):
        self.app.schedule.save(self.entry(weekday=4))
        tt = self.timetable(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
        self.assertEqual(2027, tt.get_tomorrows_classes("s1")[0].starts_at.year)

    def test_default_clock_uses_system_today(self):
        self.app.schedule.save(self.entry(weekday=datetime.now().weekday()))
        self.assertEqual(datetime.now().date(), self.app.timetable.get_todays_classes("s1")[0].starts_at.date())

    def test_profile_update_preserves_children_and_delete_cascades(self):
        self.app.memory.record_study("s1", "c", "t")
        self.app.memory.record_message("s1", "a", "user", "Hi")
        self.app.schedule.save(self.entry())
        self.app.profiles.save(StudentProfile("s1", "New name"))
        self.assertEqual(1, len(self.app.memory.history("s1")))
        self.app.profiles.delete("s1")
        for table in ("classes", "learning_events", "conversations"):
            self.assertEqual(0, self.app.db.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        self.assertEqual("Student Two", self.app.profiles.get("s2").name)

    def test_agent_dispatch_and_json(self):
        self.app.schedule.save(self.entry(weekday=datetime.now().weekday()))
        agent = self.app.agent_for("s1")
        for name in ("get_student_profile", "get_learning_history", "get_todays_classes",
                     "get_tomorrows_classes", "get_next_class"):
            result = agent.execute(name)
            self.assertTrue(result.ok)
            json.dumps(result.data)
        self.assertEqual("unknown_tool", agent.execute("shell").code)
        self.assertEqual("unavailable", agent.execute("slide_retrieval").code)
        self.assertEqual("invalid_arguments", agent.execute("get_student_profile", {"student_id": "s2"}).code)

    def test_agent_mutation_confirmation_validation(self):
        agent = self.app.agent_for("s1")
        args = dict(kind="mastery", course_id="c", topic="t", details="self-reported")
        self.assertEqual("confirmation_required", agent.execute("record_learning_event", args).code)
        self.assertEqual([], self.app.memory.history("s1"))
        self.assertTrue(agent.execute("record_learning_event", args, confirmed=True).ok)
        self.assertEqual("invalid_arguments", agent.execute("record_learning_event", {**args, "topic": 1}, confirmed=True).code)
        self.assertEqual("domain_error", agent.execute("record_learning_event", {**args, "kind": "wrong"}, confirmed=True).code)
        self.assertEqual(1, len(self.app.memory.history("s1")))

    def test_duplicate_tool_rejected(self):
        agent = ToolAgent()
        agent.register(Tool("test", "Test", {}))
        with self.assertRaises(ValueError):
            agent.register(Tool("test", "Test", {}))


class PersistenceTest(unittest.TestCase):
    def test_reopen_local_database_without_network(self):
        with tempfile.TemporaryDirectory() as directory, patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            path = Path(directory) / "nested" / "aria.sqlite3"
            with Aria(path) as app:
                app.profiles.save(StudentProfile("s", "Name"))
                app.memory.record_mastery("s", "course", "topic")
                app.memory.record_message("s", "session", "user", "Hi")
                app.schedule.save(WeeklyClass("c", "s", "course", "Class", 0, time(9), time(10)))
            with Aria(path) as app:
                self.assertEqual("Name", app.profiles.get("s").name)
                self.assertEqual(1, len(app.memory.history("s")))
                self.assertEqual(1, len(app.memory.conversations("s")))
                self.assertEqual(1, len(app.schedule.list("s")))
                self.assertTrue(app.agent_for("s").execute("get_student_profile").ok)

    def test_future_schema_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aria.sqlite3"
            with Database(path) as db:
                db.connection.execute("PRAGMA user_version = 99")
            with self.assertRaises(ValueError):
                Database(path)


if __name__ == "__main__":
    unittest.main()
