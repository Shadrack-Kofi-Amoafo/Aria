"""Structured evidence and a deterministic, replayable learning-state policy."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
import math
from typing import Protocol
from uuid import uuid4

from aria.student_memory.memory import EventKind, LearningEvent, SQLiteMemoryRepository, timestamp
from aria.validation import nonempty


@dataclass(frozen=True)
class Evidence:
    origin: str  # explicit (student), assessment (scored), inferred (detector)
    source: str  # quote, message ID, assessment reference, or detector/version
    score: float | None = None
    attempt_id: str | None = None
    misconception: str | None = None

    def validate(self, kind: EventKind) -> None:
        if kind in {EventKind.MASTERY, EventKind.DIFFICULTY, EventKind.MISCONCEPTION, EventKind.STUDY}:
            raise ValueError("Use structured event kinds for evidence; legacy flags belong to the Phase 1 API")
        if self.origin not in {"explicit", "assessment", "inferred"}:
            raise ValueError("Unknown evidence origin")
        nonempty(self.source, "source")
        if kind == EventKind.QUESTION_ANSWERED:
            if self.origin != "assessment":
                raise ValueError("Scored answers require assessment evidence")
            if type(self.score) not in (int, float) or not math.isfinite(self.score) or not 0 <= self.score <= 1:
                raise ValueError("score must be finite in [0, 1]")
            nonempty(self.attempt_id, "attempt_id")
        elif self.score is not None or self.attempt_id is not None:
            raise ValueError("Only question_answered carries a score/attempt")
        if kind in {EventKind.MISCONCEPTION_DETECTED, EventKind.MISCONCEPTION_RESOLVED}:
            nonempty(self.misconception, "misconception")
        elif self.misconception is not None:
            raise ValueError("misconception belongs to misconception events")
        if kind in {EventKind.STUDENT_EXPRESSED_CONFUSION, EventKind.STUDENT_EXPRESSED_CONFIDENCE} and self.origin != "explicit":
            raise ValueError("Student statements require explicit origin")


class EvidenceRepository(Protocol):
    def record(self, student_id: str, kind: EventKind, course_id: str, concept_id: str,
               evidence: Evidence, *, event_id: str | None = None,
               at: datetime | None = None) -> LearningEvent: ...
    def history(self, student_id: str) -> list[LearningEvent]: ...


class SQLiteEvidenceRepository:
    """Uses the existing event log. Retry IDs and attempt IDs prevent double counting."""
    def __init__(self, memory: SQLiteMemoryRepository):
        self.memory = memory
        self.db = memory.db

    def history(self, student_id):
        return self.memory.history(student_id)

    def record(self, student_id, kind, course_id, concept_id, evidence, *, event_id=None, at=None):
        self.db.require_student(student_id)
        kind = EventKind(kind)
        evidence.validate(kind)
        for key, value in (("course_id", course_id), ("concept_id", concept_id)):
            nonempty(value, key)
        event_id = str(uuid4()) if event_id is None else event_id
        nonempty(event_id, "event_id")
        occurred = timestamp(at)
        metadata = asdict(evidence)
        # Serialize checking and insertion with a write reservation.
        with self.db.connection:
            self.db.connection.execute("BEGIN IMMEDIATE")
            for old in self.history(student_id):
                same_attempt = (evidence.attempt_id is not None and old.course_id == course_id
                                and old.topic == concept_id and old.metadata.get("attempt_id") == evidence.attempt_id)
                if old.id == event_id or same_attempt:
                    if (old.kind, old.course_id, old.topic, old.metadata) != (kind, course_id, concept_id, metadata):
                        raise ValueError("Conflicting evidence retry")
                    if at is not None and old.occurred_at != occurred:
                        raise ValueError("Conflicting evidence timestamp")
                    return old
            if self.db.connection.execute("SELECT 1 FROM learning_events WHERE id = ?", (event_id,)).fetchone():
                raise ValueError("Event ID already exists")
            self.db.connection.execute(
                "INSERT INTO learning_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, student_id, kind.value, course_id, concept_id, "", occurred.isoformat(), json.dumps(metadata)))
        return LearningEvent(event_id, student_id, kind, course_id, concept_id, "", occurred, metadata)


@dataclass
class LearningState:
    student_id: str
    course_id: str
    concept_id: str
    mastery: float | None = None
    confidence: float = 0.0
    attempts: int = 0
    correct_attempts: int = 0
    incorrect_attempts: int = 0
    partial_attempts: int = 0
    last_interaction: datetime | None = None
    last_review: datetime | None = None
    next_review: datetime | None = None
    status: str = "unknown"
    reported_status: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)
    origin: str = "inferred"
    policy_version: str = "evidence-v1"


class LearningStateService:
    """Rebuild from evidence, not arbitrary setters; no persisted cache to go stale."""
    def __init__(self, repository: EvidenceRepository):
        self.repository = repository

    def states(self, student_id: str) -> list[LearningState]:
        states = {}
        scores = {}
        for event in self.repository.history(student_id):
            # Phase 1 evidence remains accessible but is not silently scored/reclassified.
            if not event.metadata:
                continue
            key = (event.course_id, event.topic)
            state = states.setdefault(key, LearningState(student_id, *key))
            state.evidence_ids.append(event.id)
            state.last_interaction = event.occurred_at
            if event.kind == EventKind.QUESTION_ANSWERED:
                score = event.metadata["score"]
                scores[key] = scores.get(key, 0) + score
                state.attempts += 1
                state.correct_attempts += score == 1
                state.incorrect_attempts += score == 0
                state.partial_attempts += 0 < score < 1
                state.mastery = scores[key] / state.attempts
                state.confidence = state.attempts / (state.attempts + 5)
                state.status = ("insufficient_evidence" if state.attempts < 3 else
                                "struggling" if state.mastery < .5 else
                                "mastered" if state.mastery >= .8 else "developing")
            if event.kind in {EventKind.QUESTION_ANSWERED, EventKind.CONCEPT_REVIEWED}:
                state.last_review = event.occurred_at
                state.next_review = event.occurred_at + timedelta(days=7 if state.status == "mastered" else 1)
            if event.kind == EventKind.STUDENT_EXPRESSED_CONFUSION:
                state.reported_status = "confused"
            if event.kind == EventKind.STUDENT_EXPRESSED_CONFIDENCE:
                state.reported_status = "confident"
            misconception = event.metadata.get("misconception")
            if event.kind == EventKind.MISCONCEPTION_DETECTED and misconception not in state.misconceptions:
                state.misconceptions.append(misconception)
            if event.kind == EventKind.MISCONCEPTION_RESOLVED and misconception in state.misconceptions:
                state.misconceptions.remove(misconception)
        return [states[key] for key in sorted(states)]

    def get(self, student_id, course_id, concept_id):
        return next((s for s in self.states(student_id) if (s.course_id, s.concept_id) == (course_id, concept_id)),
                    LearningState(student_id, course_id, concept_id))

    def difficult_topics(self, student_id):
        return [s for s in self.states(student_id) if s.status == "struggling" or s.reported_status == "confused" or s.misconceptions]
