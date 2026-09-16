"""Synthetic metadata/controlled actions only: no invented teaching material."""
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aria.app import Aria
from aria.agent import Tool, ToolAgent
from aria.agent.schema import validate
from aria.context import ContextLimits
from aria.courses import Course, Concept, CourseDocument, SourceReference, Topic
from aria.evaluation import EvaluationCase, EvaluationHarness
from aria.learning import Evidence
from aria.model import FinalResponse, MockModel, ModelInput, ToolRequest
from aria.student_memory import EventKind, StudentProfile
from aria.timetable import WeeklyClass

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


class Phase2Test(unittest.TestCase):
    def setUp(self):
        self.app = Aria(":memory:")
        self.addCleanup(self.app.close)
        self.app.profiles.save(StudentProfile("a", "Student A"))
        self.app.profiles.save(StudentProfile("b", "Student B"))
        self.app.timetable.clock = lambda: NOW
        self.app.timetable.zone = timezone.utc

    def answer(self, score, attempt="attempt-1", student="a", at=NOW, **kwargs):
        return self.app.evidence.record(student, EventKind.QUESTION_ANSWERED, "course", "concept",
                                        Evidence("assessment", "test-assessment", score, attempt), at=at, **kwargs)

    def course_fixture(self, course="course"):
        records = [Course(course, "Fixture metadata"),
                   CourseDocument("doc", course, "Fixture document", "data/fixture.txt", "text/plain", "fixture-hash"),
                   SourceReference("source", course, "doc", page=1, slide=2, section="fixture", source_text="test fixture", source_image="data/fixture.png"),
                   Topic("topic", course, "Fixture topic", ["source"]),
                   Concept("concept", course, "topic", "Fixture concept", ["source"])]
        for record in records:
            self.app.courses.save(record)
        return records

    def script(self, *actions, **options):
        return self.app.orchestrator(MockModel(actions), **options)

    def evidence_args(self, **changes):
        args = dict(kind="student_expressed_confusion", course_id="course", concept_id="concept",
                    event_id="report-1", evidence={"origin": "explicit", "source": "Student reported difficulty"})
        args.update(changes)
        return args

    def test_schema_version_and_reopen(self):
        self.assertEqual(2, self.app.db.connection.execute("PRAGMA user_version").fetchone()[0])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            with Aria(path) as app:
                app.profiles.save(StudentProfile("s", "Name"))
                event = app.evidence.record("s", EventKind.CONCEPT_VIEWED, "c", "t", Evidence("explicit", "message:1"), at=NOW)
                fact = app.explicit_memory.record("s", "preference", "diagrams", "student quote", at=NOW)
                app.courses.save(Course("c", "Metadata"))
            with Aria(path) as app:
                self.assertEqual([event], app.evidence.history("s"))
                self.assertEqual([fact], app.explicit_memory.current("s"))
                self.assertEqual(Course("c", "Metadata"), app.courses.get("c", "Course", "c"))

    def test_v1_migration_preserves_all_legacy_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.sqlite3"
            # A real v1 database, initialized using the unchanged v1 schema path.
            with patch("aria.migrations.migrate_v2"):
                with Aria(path) as app:
                    app.profiles.save(StudentProfile("s", "Name"))
                    app.memory.record_study("s", "c", "t")
                    app.memory.record_message("s", "session", "user", "Legacy")
                    app.schedule.save(WeeklyClass("class", "s", "c", "Title", 0, time(9), time(10)))
            with Aria(path) as app:
                self.assertEqual({}, app.memory.history("s")[0].metadata)
                self.assertEqual([], app.learning.states("s"))
                self.assertEqual(1, len(app.memory.conversations("s")))
                self.assertEqual(1, len(app.schedule.list("s")))
                self.assertEqual("Name", app.profiles.get("s").name)
            with Aria(path) as app:  # migration is not repeated
                self.assertEqual(1, len(app.memory.history("s")))

    def test_explicit_vs_inferred_and_legacy(self):
        fact = self.app.explicit_memory.record("a", "explanation_preference", "diagrams", "I prefer diagrams", at=NOW)
        self.app.evidence.record("a", EventKind.STUDENT_EXPRESSED_CONFUSION, "course", "concept", Evidence("explicit", "student quote"), at=NOW)
        state = self.app.learning.get("a", "course", "concept")
        self.assertEqual("confused", state.reported_status)
        self.assertEqual("unknown", state.status)
        self.assertIsNone(state.mastery)
        self.assertEqual("explicit", fact.origin)
        self.assertEqual("inferred", state.origin)
        self.answer(0)
        self.assertEqual([fact], self.app.explicit_memory.current("a"))
        self.assertEqual([], self.app.explicit_memory.current("b"))
        self.assertEqual([], self.app.learning.states("b"))
        self.app.memory.record_mastery("a", "legacy", "topic")
        self.assertIsNone(self.app.learning.get("a", "legacy", "topic").mastery)
        self.assertIn(("legacy", "topic"), self.app.student_memory.topics_mastered("a"))

    def test_explicit_current_by_event_time_not_write_order(self):
        current = self.app.explicit_memory.record("a", "preference", "new", "quote2", at=NOW)
        self.app.explicit_memory.record("a", "preference", "old", "quote1", at=NOW - timedelta(days=1))
        self.assertEqual([current], self.app.explicit_memory.current("a"))
        self.assertEqual(2, len(self.app.explicit_memory.history("a")))
        with self.assertRaises(ValueError):
            self.app.explicit_memory.record("a", "preference", "new", "")

    def test_mastery_counts_confidence_review_and_replay(self):
        for i, score in enumerate((1, 0, .5)):
            self.answer(score, f"attempt-{i}", at=NOW + timedelta(minutes=i))
        state = self.app.learning.get("a", "course", "concept")
        self.assertEqual((3, 1, 1, 1), (state.attempts, state.correct_attempts, state.incorrect_attempts, state.partial_attempts))
        self.assertEqual(.5, state.mastery)
        self.assertEqual(3 / 8, state.confidence)
        self.assertEqual("developing", state.status)
        self.assertEqual(state.last_review + timedelta(days=1), state.next_review)
        self.assertEqual(state, self.app.learning.get("a", "course", "concept"))
        self.assertEqual(3, len(state.evidence_ids))

    def test_repeated_evidence_and_mastered_interval(self):
        for i in range(3):
            self.answer(1, str(i))
        state = self.app.learning.get("a", "course", "concept")
        self.assertEqual("mastered", state.status)
        self.assertEqual(NOW + timedelta(days=7), state.next_review)
        for i in range(7):
            self.answer(0, f"wrong-{i}")
        self.assertEqual("struggling", self.app.learning.get("a", "course", "concept").status)
        self.assertEqual(1, len(self.app.learning.difficult_topics("a")))

    def test_backdated_events_and_misconception_resolution(self):
        self.app.evidence.record("a", EventKind.MISCONCEPTION_RESOLVED, "course", "concept",
                                 Evidence("inferred", "review:v1", misconception="m"), at=NOW)
        self.app.evidence.record("a", EventKind.MISCONCEPTION_DETECTED, "course", "concept",
                                 Evidence("inferred", "detector:v1", misconception="m"), at=NOW - timedelta(days=1))
        state = self.app.learning.get("a", "course", "concept")
        self.assertEqual([], state.misconceptions)
        self.assertEqual(NOW, state.last_interaction)
        self.assertEqual(2, len(state.evidence_ids))

    def test_idempotent_retry_attempt_and_cross_student_ids(self):
        first = self.answer(1, event_id="one")
        self.assertEqual(first, self.answer(1, event_id="one"))
        self.assertEqual(first, self.answer(1, event_id="other"))
        with self.assertRaises(ValueError):
            self.answer(0, event_id="one")
        with self.assertRaises(ValueError):
            self.answer(1, student="b", event_id="one")
        self.assertEqual(1, self.app.learning.get("a", "course", "concept").attempts)
        self.assertEqual([], self.app.evidence.history("b"))

    def test_evidence_validation_is_atomic(self):
        for evidence in (Evidence("explicit", "quote", 1, "id"), Evidence("assessment", "source", True, "id"),
                         Evidence("assessment", "source", float("nan"), "id"), Evidence("assessment", "source", 2, "id"),
                         Evidence("assessment", "source", .5), Evidence("assessment", "", .5, "id")):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.app.evidence.record("a", EventKind.QUESTION_ANSWERED, "c", "t", evidence)
        with self.assertRaises(ValueError):
            self.app.evidence.record("a", EventKind.STUDENT_EXPRESSED_CONFUSION, "c", "t", Evidence("inferred", "guess"))
        with self.assertRaises(ValueError):
            self.answer(1, at=datetime(2026, 9, 16))
        self.assertEqual([], self.app.evidence.history("a"))

    def test_student_delete_cascades_new_private_data(self):
        self.answer(1)
        self.app.explicit_memory.record("a", "key", "value", "quote")
        self.app.profiles.delete("a")
        for table in ("explicit_memories", "learning_events"):
            self.assertEqual(0, self.app.db.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        with self.assertRaises(KeyError):
            self.app.learning.states("a")

    def test_course_roundtrip_and_provenance(self):
        for record in self.course_fixture():
            course_id = record.id if isinstance(record, Course) else record.course_id
            self.assertEqual(record, self.app.courses.get(course_id, type(record).__name__, record.id))
        self.app.courses.save(Course("course", "Fixture metadata", ["source"]))
        source = self.app.courses.get("course", "SourceReference", "source")
        self.assertEqual((1, 2, "fixture"), (source.page, source.slide, source.section))
        self.assertEqual("data/fixture.png", source.source_image)
        self.assertEqual([], self.app.courses.list("other", "Concept"))

    def test_source_revision_cannot_rewrite_existing_provenance(self):
        records = self.course_fixture()
        document, source = records[1:3]
        for changed in (replace(document, local_path="data/replacement.txt"), replace(source, page=99)):
            with self.assertRaises(ValueError):
                self.app.courses.save(changed)
        self.app.courses.save(source)  # exact retry is safe
        self.assertEqual(source, self.app.courses.get("course", "SourceReference", source.id))

    def test_course_relations_and_validation(self):
        self.course_fixture()
        related = Concept("second", "course", "topic", "Second fixture", ["source"], ["concept"], ["concept"], ["concept"])
        self.app.courses.save(related)
        self.assertEqual(related, self.app.courses.get("course", "Concept", "second"))
        for invalid in (replace(related, source_ids=[]), replace(related, source_ids=["missing"]),
                        replace(related, prerequisites=["second"]), replace(related, topic_id="missing"),
                        SourceReference("bad", "course", "doc", page=0), SourceReference("bad", "course", "doc"),
                        SourceReference("bad", "course", "missing", slide=1),
                        CourseDocument("bad", "course", "Title", "https://invalid", "text/plain")):
            with self.subTest(invalid=invalid), self.assertRaises((ValueError, KeyError)):
                self.app.courses.save(invalid)
        self.app.courses.save(Course("other", "Other"))
        with self.assertRaises(KeyError):
            self.app.courses.save(Topic("topic", "other", "Other", ["source"]))

    def test_tool_contracts_and_schema_validation(self):
        agent = self.app.agent_for("a")
        for contract in agent.describe_tools():
            for key in ("input_schema", "output_schema", "classification", "student_scope", "confirmation_required"):
                self.assertIn(key, contract)
            self.assertTrue(contract["output_schema"])
        args = self.evidence_args()
        for wrong in ({**args, "student_id": "b"}, {**args, "evidence": {"origin": "explicit"}},
                      {**args, "evidence": {"origin": "explicit", "source": "s", "score": True}},
                      {**args, "evidence": {"origin": "explicit", "source": "s", "extra": 1}}):
            self.assertEqual("invalid_arguments", agent.execute("record_evidence", wrong, confirmed=True).code)
        self.assertEqual("confirmation_required", agent.execute("record_evidence", args, confirmed="yes").code)
        self.assertTrue(agent.execute("record_learning_event", args, confirmed=True).ok)
        self.assertEqual([], self.app.evidence.history("b"))
        self.assertEqual("confused", agent.execute("get_mastery", {"course_id": "course", "concept_id": "concept"}).data["reported_status"])

    def test_tool_output_and_execution_errors(self):
        agent = ToolAgent()
        agent.register(Tool("bad-output", "Test", {}, lambda: [], output_schema={"type": "object"}))
        self.assertEqual("invalid_output", agent.execute("bad-output").code)
        def broken():
            raise RuntimeError("private path must not leak")
        agent.register(Tool("broken", "Test", {}, broken))
        self.assertEqual("execution_error", agent.execute("broken").code)
        self.assertNotIn("private", agent.execute("broken").error)

    def test_schema_nested_ranges_arrays_null(self):
        schema = {"type": "array", "maxItems": 2, "items": {"anyOf": [{"type": "null"}, {"type": "number", "minimum": 0, "maximum": 1}]}}
        validate([None, .5], schema)
        for value in ([True], [float("inf")], [-1], [0, 0, 0]):
            with self.assertRaises(ValueError):
                validate(value, schema)

    def test_update_mastery_tool_uses_evidence_not_manual_values(self):
        args = self.evidence_args(kind="question_answered", evidence={"origin": "assessment", "source": "quiz-result", "score": 1, "attempt_id": "q1"})
        result = self.app.agent_for("a").execute("update_mastery", args, confirmed=True)
        self.assertTrue(result.ok)
        self.assertEqual(1, result.data["attempts"])
        self.assertEqual("invalid_arguments", self.app.agent_for("a").execute("update_mastery", {"mastery": 1}, confirmed=True).code)

    def test_context_is_bounded_deterministic_and_scoped(self):
        for i in range(25):
            self.app.memory.record_message("a", "session", "user", f"{i}:" + "x" * 1000, at=NOW)
        self.app.memory.record_message("a", "other", "user", "OTHER SESSION SECRET")
        self.app.memory.record_message("b", "session", "user", "OTHER STUDENT SECRET")
        self.app.explicit_memory.record("a", "preference", "diagrams", "quote", at=NOW)
        self.answer(.5)
        builder = self.app.context_builder(limits=ContextLimits(messages=3, text_chars=100))
        context = builder.build_context("a", "session", "concept")
        self.assertEqual(context, builder.build_context("a", "session", "concept"))
        self.assertEqual(3, len(context["conversation"]))
        self.assertLess(len(context["conversation"][0]["content"]), 110)
        encoded = json.dumps(context)
        self.assertNotIn("SECRET", encoded)
        self.assertEqual("explicit", context["explicit_memory"][0]["origin"])
        self.assertEqual("inferred", context["learning_state"][0]["origin"])
        self.assertFalse(context["course_knowledge"]["available"])
        self.assertEqual(NOW.isoformat(), context["current_time"])
        self.assertLess(len(encoded), 48000)
        with self.assertRaises(ValueError):
            builder.build_context("a", "session", "x" * 4001)
        with self.assertRaises(ValueError):
            self.app.context_builder(limits=ContextLimits(total_chars=1)).build_context("a", "s", "Hi")
        with self.assertRaises(ValueError):
            ContextLimits(messages=0)

    def test_context_relevance_and_timetable_authority(self):
        self.answer(.5)
        self.app.evidence.record("a", EventKind.CONCEPT_VIEWED, "c", "other", Evidence("explicit", "view"), at=NOW + timedelta(days=1))
        self.app.schedule.save(WeeklyClass("class", "a", "course", "Class metadata", 3, time(9), time(10)))
        context = self.app.context_builder(limits=ContextLimits(concepts=1)).build_context("a", "s", "concept")
        self.assertEqual("concept", context["learning_state"][0]["concept_id"])
        self.assertEqual("2026-09-17T09:00:00+00:00", context["timetable"]["tomorrow"][0]["starts_at"])

    def test_mock_script_stream_reset_and_input_snapshot(self):
        model = MockModel([ToolRequest("get_next_class"), FinalResponse("Scripted")])
        request = ModelInput({"x": []})
        self.assertIsInstance(model.generate(request), ToolRequest)
        request.context["x"].append(1)
        self.assertEqual([], model.requests[0].context["x"])
        self.assertEqual([FinalResponse("Scripted")], list(model.stream(request)))
        self.assertEqual("unavailable", model.generate(request).status)
        model.reset()
        self.assertEqual([], model.requests)
        self.assertIsInstance(model.generate(request), ToolRequest)

    def test_agent_tool_result_feedback_and_conversation(self):
        model = MockModel([ToolRequest("get_tomorrow_classes", call_id="next"), FinalResponse("Script completed")])
        result = self.app.orchestrator(model).run("a", "s", "Tomorrow?")
        self.assertEqual("final", result.status)
        self.assertEqual(2, result.model_calls)
        self.assertEqual("get_tomorrow_classes", model.requests[1].tool_results[0]["name"])
        self.assertEqual(["user", "tool", "assistant"], [m.role for m in self.app.memory.conversations("a", "s")])
        self.assertEqual([], self.app.memory.conversations("b", "s"))
        self.assertEqual([], self.app.evidence.history("a"))

    def test_unavailable_cannot_turn_into_fabricated_success(self):
        for name in ("search_course", "get_concept", "get_course_source", "slide_retrieval"):
            result = self.script(ToolRequest(name), FinalResponse("Pretend course answer")).run("a", name, "Need course information")
            self.assertEqual("unavailable", result.status)
            self.assertNotIn("Pretend", result.response)
            self.assertEqual("unavailable", result.trace[0]["result"]["code"])

    def test_agent_confirmation_updates_and_duplicate_calls(self):
        action = ToolRequest("record_evidence", self.evidence_args(), "write-1")
        denied = self.script(action, FinalResponse("Saved")).run("a", "denied", "Difficulty report")
        self.assertEqual("confirmation_required", denied.trace[0]["result"]["code"])
        self.assertEqual([], self.app.evidence.history("a"))
        approvals = []
        def confirm(student, request):
            approvals.append((student, request.name))
            return True
        result = self.script(action, action, FinalResponse("Saved"), confirm=confirm).run("a", "approved", "Difficulty report")
        self.assertEqual([("a", "record_evidence")], approvals)
        self.assertEqual("duplicate_call", result.trace[1]["result"]["code"])
        self.assertEqual(1, len(self.app.evidence.history("a")))
        self.assertEqual("confused", self.app.learning.get("a", "course", "concept").reported_status)

    def test_loop_limits_invalid_actions_and_provider_failure(self):
        result = self.script(ToolRequest("get_next_class"), max_steps=1).run("a", "limit", "Next?")
        self.assertEqual("step_limit", result.status)
        for action in ("not an action", FinalResponse(""), ToolRequest("get_next_class", {"bad": float("nan")})):
            self.assertEqual("invalid_action", self.script(action).run("a", "invalid", "Hi").status)
        class BrokenProvider:
            def generate(self, request):
                raise RuntimeError("private error")
        self.assertEqual("provider_error", self.app.orchestrator(BrokenProvider()).run("a", "error", "Hi").status)
        result = self.script(ToolRequest("get_student_profile"), FinalResponse("OK"), max_tool_chars=10).run("a", "large", "Profile?")
        self.assertEqual("result_too_large", result.trace[0]["result"]["code"])

    def test_evaluation_cases_and_failure_reporting(self):
        harness = EvaluationHarness()
        cases = [
            (EvaluationCase("timetable", "a", "eval1", "Tomorrow?", ("get_tomorrow_classes",)),
             self.script(ToolRequest("get_tomorrow_classes"), FinalResponse("Script complete"))),
            (EvaluationCase("unknown", "a", "eval2", "Course question", ("get_concept",), "unavailable"),
             self.script(ToolRequest("get_concept"), FinalResponse("Invented"))),
            (EvaluationCase("difficulty", "a", "eval3", "Difficulty report", ("record_evidence",), "final",
                            ("student_expressed_confusion",), {"reported_not_scored": lambda result, context:
                             self.app.learning.get("a", "course", "concept").reported_status == "confused" and
                             self.app.learning.get("a", "course", "concept").mastery is None}),
             self.script(ToolRequest("record_evidence", self.evidence_args()), FinalResponse("Recorded"), confirm=lambda s, r: True)),
        ]
        results = [harness.run_case(case, agent, self.app.evidence) for case, agent in cases]
        self.assertTrue(all(r.passed for r in results), results)
        self.assertEqual({"cases": 3, "passed": 3, "pass_rate": 1.0}, harness.summarize(results))
        failed = harness.run_case(EvaluationCase("wrong", "a", "fail", "Hi", ("get_next_class",)), self.script(FinalResponse("Hi")), self.app.evidence)
        self.assertFalse(failed.passed)
        self.assertFalse(failed.checks["tool_selection"])

    def test_legacy_api_cannot_silently_drop_structured_metadata(self):
        with self.assertRaises(ValueError):
            self.app.memory.record_event("a", EventKind.QUESTION_ANSWERED, "c", "t")
        self.assertEqual([], self.app.evidence.history("a"))

    def test_course_records_persist_after_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "courses.sqlite3"
            with Aria(path) as app:
                for record in self.course_fixture():
                    app.courses.save(record)
            with Aria(path) as app:
                concept = app.courses.get("course", "Concept", "concept")
                source = app.courses.get("course", "SourceReference", concept.source_ids[0])
                document = app.courses.get("course", "CourseDocument", source.document_id)
                self.assertEqual("data/fixture.txt", document.local_path)
                self.assertEqual(2, source.slide)

    def test_model_request_cannot_override_student_or_confirmation(self):
        for extra in ({"student_id": "b"}, {"confirmed": True}):
            action = ToolRequest("record_evidence", {**self.evidence_args(), **extra})
            result = self.script(action, FinalResponse("Saved"), confirm=lambda s, r: True).run("a", "scope", "Report")
            self.assertEqual("invalid_arguments", result.trace[0]["result"]["code"])
        self.assertEqual([], self.app.evidence.history("a"))
        self.assertEqual([], self.app.evidence.history("b"))

    def test_confirmation_failure_and_unavailable_mock(self):
        def broken_confirmation(student, request):
            raise RuntimeError("UI failed")
        result = self.script(ToolRequest("record_evidence", self.evidence_args()), FinalResponse("Saved"),
                             confirm=broken_confirmation).run("a", "confirmation", "Report")
        self.assertEqual("confirmation_required", result.trace[0]["result"]["code"])
        self.assertEqual("unavailable", self.script().run("a", "exhausted", "Hi").status)

    def test_evaluation_reproducibility_with_fresh_sessions(self):
        reports = []
        for _ in range(2):
            with Aria(":memory:") as app:
                app.profiles.save(StudentProfile("s", "Name"))
                app.timetable.clock = lambda: NOW
                case = EvaluationCase("repeatable", "s", "session", "Tomorrow?", ("get_tomorrow_classes",))
                agent = app.orchestrator(MockModel([ToolRequest("get_tomorrow_classes"), FinalResponse("Scripted")]))
                reports.append(EvaluationHarness().run_case(case, agent, app.evidence))
        self.assertEqual(reports[0], reports[1])
        self.assertTrue(reports[0].passed)

    def test_phase2_offline_core(self):
        with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            self.course_fixture()
            self.answer(1)
            result = self.script(ToolRequest("get_mastery", {"course_id": "course", "concept_id": "concept"}), FinalResponse("Scripted result")).run("a", "offline", "Progress?")
            self.assertEqual("final", result.status)


if __name__ == "__main__":
    unittest.main()
