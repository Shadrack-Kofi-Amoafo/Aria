# Phase 1 architecture and integration guide

## Layout and dependency direction

```text
src/aria/
  app.py                composition root and database lifecycle
  storage.py            SQLite schema v1, connection, student identity guard
  validation.py         domain validators
  model/                LanguageModel, ChatMessage ports
  agent/                Tool, ToolResult, ToolAgent, student-scoped tool wiring
  courses/              CourseCatalog, CourseIngestor ports
  retrieval/            SlideRetriever port, provenance-bearing SlideExcerpt
  student_memory/       profiles, conversations, events, derived topic state
  timetable/            weekly classes, repository, date-aware query service
  teaching/             LessonPlanner port
  assessment/           QuizProvider port
  visuals/              VisualProvider port
  voice/                SpeechRecognizer / SpeechSynthesizer ports
  training/             Trainer port
  evaluation/           Evaluator port
```

`Protocol` contracts isolate backends from consumers. Domain data uses dataclasses;
SQLite adapters implement profile, memory and schedule repositories. `Aria` is a
convenience composition root, not a global singleton: callers may directly compose
`build_agent()` with alternative repository implementations and `Timetable` with a
custom `ScheduleRepository`. Nothing opens a database or loads a model on import.
Future backends should live in their owning packages and be wired at the composition
root, not imported into domain repositories.

## Student state and memory

A profile stores stable ID, names, course IDs, learning preferences, strengths and
weaknesses. Course IDs are opaque metadata: an ingested course catalog is not yet
required and these IDs are not catalog foreign keys. A schedule/event may refer to
a course not currently listed in the profile, e.g. historical or independent study.

Conversations contain student ID, session ID, role, content and occurrence time.
Supported roles are `user`, `assistant`, `system`, `tool`. Recording is explicit;
merely invoking read tools does not record a conversation or create learning events.

Learning events are append-only with one of:

- `mastery`: evidence that the topic was mastered
- `difficulty`: a reported difficulty
- `misconception`: a reported misunderstanding
- `study`: activity/history, without changing current topic status

Use `record_mastery()`, `record_difficulty()`, `record_misconception()`,
`record_study()`, or the generic `record_event()`. Details are free text for provenance
such as “self-reported” or a future assessment reference. This is not a validated
psychometric mastery score. No mastery is inferred automatically.

`history()` returns oldest-first evidence ordered by UTC event time, breaking ties
by insertion order. Input timestamps must be timezone-aware; omitted timestamps use
the actual system clock. Backdated evidence does not overwrite more recent evidence.
`StudentMemory` uses the latest non-study event per **(course ID, topic)** to derive
mastered and struggled-with topic lists. Topics match exactly (case-sensitive).
`get_student_profile` combines metadata, learning history and derived topic lists.
Historical difficulties remain in history after subsequent mastery.

SQLite uses foreign keys and parameterized queries. IDs must reference an existing
student; missing students raise `KeyError`. Profile upserts do not delete child data.
Each write has its own transaction. Schema version is stored in `PRAGMA user_version`;
future schema changes need explicit migrations and migration tests. Unknown versions
are rejected rather than silently altered. Use one connection per worker/thread.
History reads currently return all records; pagination/retention are future work.

## Timetable semantics

`WeeklyClass` is recurring local wall time: weekday **Monday=0 through Sunday=6**.
Class IDs are globally unique; another student cannot overwrite an existing ID.
Classes must start and end on the same day with end strictly later than start.
Overnight classes must be split into two entries. Overlaps are permitted so real
schedule conflicts are retained; results sort by start time then class ID.

- `classes_on(student_id, date)` returns timezone-aware occurrences for that date.
- `get_todays_classes()` and `get_tomorrows_classes()` include all classes on the
  respective local calendar day, including already-finished classes today.
- `get_next_class()` returns the earliest start **at or after now**. An ongoing class
  is excluded. If all today's classes have started, it searches through the same
  weekday next week. Empty schedule returns `None`; day queries return `[]`.

Defaults use `datetime.now().astimezone()` and system-local conversion for each
occurrence date, rather than caching today's UTC offset. Configure the host timezone
correctly. For tests or an explicit deployment timezone, inject a timezone-aware
`clock` callable and `zone=ZoneInfo("Area/City")`. A supplied clock without a zone
still gets converted to the system timezone. ZoneInfo uses the host timezone database
(which must be installed if named timezones are needed).

This is a weekly calendar, not a term calendar: no holiday exceptions, cancellations,
semester bounds, reminders or travel-timezone persistence yet. Times in the repeated
or nonexistent hour during a DST transition follow Python/OS resolution; there is no
special ambiguity/gap policy in Phase 1. Typical daytime classes are unaffected.
Add explicit transition validation before supporting overnight/early-hours schedules.

## Agent tool boundary

`build_agent(student_id, profiles, memory, timetable)` binds student identity in
closures: tool arguments cannot select another student. `describe_tools()` exposes
names, parameter types, required fields, availability and write flags. All declared
arguments are required; unknown arguments and wrong types are rejected. Results are
`ToolResult(ok, data, error, code)` with JSON-compatible data. Error codes include
`unknown_tool`, `unavailable`, `invalid_arguments`, `confirmation_required`, and
`domain_error`. Unexpected programming/storage failures propagate to the caller;
there is no retry that could silently duplicate writes.

Available read tools: `get_student_profile`, `get_learning_history`,
`get_conversations`, `get_todays_classes`, `get_tomorrows_classes`, `get_next_class`.
Write tools: `record_message`, `record_learning_event`. Caller must pass
`confirmed=True` separately from tool arguments. A future UI must obtain real user
confirmation; never let a model supply this flag. This guard is not authentication.

Reserved tools `course_search`, `slide_retrieval`, `create_quiz`, `plan_lesson`, and
`create_diagram` have no handler. They return `unavailable`, not synthetic content.
The registry is explicit and rejects duplicate tool names. When implementing a
capability, wire its handler in `build_agent` (or compose a custom registry) in place
of that placeholder. There is no shell, dynamic code execution, filesystem tool,
or autonomous reasoning loop.

## Plugging in a local model (future phase)

1. Implement `LanguageModel.generate(messages, max_tokens=...)` in a local adapter
   (e.g. a locally installed inference runtime reading a pre-provisioned model file).
2. Accept the model path and runtime settings as configuration. Target 1.7B–4B models;
   quantization, hardware and quality need measurement, not assumptions. Never
   silently fall back to a cloud model or auto-download at runtime.
3. Load the adapter explicitly in the composition root. Keep runtime-specific prompt
   templates/tokenization inside the adapter so changing the model leaves storage
   and domain APIs intact.
4. Add a separate orchestrator for chat and model-generated tool requests. Validate
   requests through `ToolAgent`, enforce limits and confirmation, and feed tool results
   back to the model. Treat course content as untrusted data, not tool instructions.
5. Contract-test using a fake `LanguageModel`; evaluate real models using local cases
   via `Evaluator`. Test no-network operation and hallucination/grounding behavior
   before enabling real tutoring. No inference adapter is shipped in Phase 1.

## Plugging in course ingestion/retrieval (future phase)

1. Keep user slides in a configurable local course-data directory (default future
   convention `data/courses/<course-id>/`); no source documents in Git.
2. Implement `CourseIngestor.ingest(course_id, source: Path)`: validate local input,
   extract text and preserve path/page or slide number. Handle scanned documents
   separately with optional local OCR. Version source hashes to avoid stale indexes.
3. Implement `CourseCatalog.search()` over local metadata. Implement `SlideRetriever`
   over a replaceable local index, returning `SlideExcerpt` with source provenance.
   Embeddings/vector search are **not implemented now** and should be optional adapters.
4. Wire the catalog/retriever into the reserved tool handlers. Empty search results
   should stay empty; unavailable course evidence must never become invented slides.
5. Implement lesson/quiz providers using those excerpts, expose citations, and record
   learning events only from explicit student input or a documented assessment policy.
6. Test ingestion with controlled fixtures, retrieval provenance and local-only model
   assets. Voice, visuals and training remain separate optional ports. Do not derive
   a fine-tuning dataset from private memory without a separate consent/design phase.

## Testing

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m compileall -q src tests examples
```

Tests cover validation, profile independence/upsert/delete, conversation isolation,
UTC event ordering, mastery state, persistence after reopening, schema rejection,
empty schedules, updates/deletion, date/week/year boundaries, exact-start semantics,
DST offsets, tool validation/confirmation and unavailable capabilities. A persistence
integration test disables socket creation while exercising core services. The CI
template at `docs/github-actions-tests.yml.example` runs tests on Python 3.11 and
3.12. To enable it, copy it to `.github/workflows/tests.yml` using a GitHub connection
with workflow-write permission (the current connection cannot create workflows).
CI infrastructure is not a runtime cloud dependency. Tests need no real student records, slides, model files or credentials.
