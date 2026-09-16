# ARIA — Adaptive Reasoning & Intelligence Assistant

Phase 2 infrastructure for an **offline personal AI tutor** (built on Phase 1). Python 3.11+; SQLite and
standard-library runtime only. No API keys, network service, model downloads, or
course content required.

## Run locally

From the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python examples/local_demo.py
```

These commands require no package installation or network. On Windows PowerShell,
set `$env:PYTHONPATH = "src"` first, then run the Python commands without the prefix.
Optional development install: `python -m pip install -e .` (build tooling may need
to be installed/downloaded; it is not required for the commands above).

The demo uses an in-memory database and generic schedule metadata, not teaching
content. The library's default persistent database is `data/aria.sqlite3`, relative
to the working directory. Pass an explicit path for a stable deployment location.

## Phase 2 additions

- Structured, source-labeled learning evidence and replayable concept state.
- Explicit student facts separated from inferred mastery, confidence and difficulties.
- Persisted course/topic/concept/document/source contracts, with validated provenance.
- Formal tool schemas, student scope, output validation and predictable error codes.
- Bounded deterministic context builder and a swappable structured `ModelProvider`.
- Scripted `MockModel`, bounded agent loop and behavioral evaluation harness.
- Additive SQLite v1 → v2 migration; existing public APIs and Phase 1 tests retained.

**ARIA cannot yet tutor from real course material.** The mock is a test script player,
not an LLM. Course metadata persistence is not course ingestion or RAG.

```sh
PYTHONPATH=src python examples/phase2_demo.py
```

See the [Phase 2 guide](docs/phase2.md) for learning policy, tool contracts, provider
integration, orchestration, evaluation and limitations.

## Existing foundation

- Student profiles: name/preferred name, course IDs, preferences, strengths and weaknesses.
- Local conversations, study history, mastery, difficulties and misconceptions.
  Mastered/struggled topics are derived from recorded evidence, not duplicated in profiles.
- Weekly timetable storage, today's/tomorrow's classes, and next-class lookup using
  the **local system clock and timezone** by default.
- Student-scoped tool registry with validated inputs and confirmation-gated writes.
- Typed extension ports for models, course ingestion/search, retrieval, quizzes,
  lesson planning, visuals, voice, training and evaluation.
- Unit tests and a ready-to-enable GitHub Actions workflow template.

**Not implemented:** model inference, autonomous tutoring, course parsing/RAG,
voice, fine-tuning, generated lessons/quizzes/images, or a UI. Placeholder tools
explicitly report `unavailable`. No course material is hard-coded.

## Minimal API example

```python
from datetime import time
from aria.app import Aria
from aria.student_memory import StudentProfile
from aria.timetable import WeeklyClass

with Aria("data/aria.sqlite3") as aria:
    aria.profiles.save(StudentProfile(
        student_id="student-1", name="Alex", preferred_name="Alex",
        courses=["my-course"], learning_preferences=["worked examples"],
    ))
    aria.schedule.save(WeeklyClass(
        id="monday-class", student_id="student-1", course_id="my-course",
        title="My course", weekday=0, starts_at=time(9), ends_at=time(10),
    ))
    print(aria.timetable.get_todays_classes("student-1"))
    print(aria.timetable.get_tomorrows_classes("student-1"))
    print(aria.timetable.get_next_class("student-1"))

    aria.memory.record_study("student-1", "my-course", "user-selected topic", "Reviewed notes")
    aria.memory.record_difficulty("student-1", "my-course", "user-selected topic")
    aria.memory.record_mastery("student-1", "my-course", "user-selected topic", "Self-reported")
    print(aria.student_memory.topics_mastered("student-1"))
    print(aria.memory.history("student-1"))

    agent = aria.agent_for("student-1")
    print(agent.execute("get_student_profile"))
    print(agent.describe_tools())
```

Repository writes are explicit. The profile `save()` operation replaces all profile
metadata for that student, but preserves their learning history and schedule.
Do not re-save a default profile at each startup; load it with `profiles.get()`.

## Data and privacy

All core data stays in the selected local SQLite file. `data/` and `models/` are
Git-ignored. There is no telemetry. SQLite data is **not encrypted**: use OS file
permissions and disk encryption for sensitive student records. The local library
is not an authentication boundary and should not be exposed directly as a public API.
`profiles.delete(student_id)` removes the profile and cascades to its conversations,
events and schedule. This is logical deletion, not guaranteed forensic erasure;
backups can retain records. Back up the database while the application is closed.

See the [Phase 1 architecture](docs/architecture.md) for the original repository and
timetable semantics, and the [Phase 2 guide](docs/phase2.md) for current additions.
