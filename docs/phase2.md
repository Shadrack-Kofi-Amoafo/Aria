# Phase 2 — learning core and agent infrastructure

ARIA still has **no actual model inference or course ingestion**. This phase extends
Phase 1; it does not replace its profile, conversation, timetable or repository APIs.
All functionality runs with Python 3.11+ and SQLite, with no new dependencies.

## Architecture

```text
Student / trusted caller
  → Orchestrator
    → ContextBuilder (bounded, deterministic selection)
    → ModelProvider (MockModel today; local adapter later)
    → ToolAgent (schema validation, student scope, confirmation)
      → existing profile/memory/timetable services
      → evidence repository → LearningStateService
      → unavailable course/retrieval capabilities
    → tool result → ModelProvider → final response
```

New components:

- `aria.learning`: Evidence, LearningState, repository protocol/SQLite adapter,
  deterministic replay service.
- `aria.student_memory.explicit`: explicit fact history/current view and protocol.
- `aria.courses.records`: metadata/provenance contracts and local repository.
- `aria.migrations`: additive SQLite migration.
- `aria.agent.schema`, `learning_tools`: JSON schema subset and enhanced tool contracts.
- `aria.context`: context limits and builder.
- `aria.model.provider`: structured provider actions, generate/stream and scripted mock.
- `aria.agent.orchestrator`: bounded synchronous loop.
- `aria.evaluation.harness`: model-independent behavioral cases/results.

`Aria` wires adapters; protocols and injected factories keep components replaceable.
`Aria.agent_for()` extends the original `build_agent()` registry. Direct Phase 1
`build_agent()` use remains possible. The legacy plain-text `LanguageModel` contract
is retained; new orchestration uses the structured `ModelProvider` contract instead.

## Storage and migration

Opening an existing database automatically migrates `user_version=1` to **2**:

- Existing `learning_events` receives a `metadata` JSON column, default `{}`.
- `explicit_memories` stores student-owned fact records, source and occurrence time.
- `course_records` stores typed metadata keyed by `(course_id, kind, id)`.
- All Phase 1 tables and IDs remain intact. Future unknown schema versions fail closed.

The v2 migration is transactional. Back up a closed database before upgrading. Tests
exercise a real v1 schema with profiles, messages, events and schedule records, then
reopen twice to verify preservation and migration idempotence.

Private facts/events use student foreign keys with cascading deletion. Course records
are a **shared local catalog**, not per-student private notes, and survive student
deletion. Course IDs/reference integrity is checked transactionally in the repository;
there is no direct SQL mutation API or course-delete API. As in Phase 1, SQLite is
not encrypted and this library is not an authentication boundary.

## Explicit memory versus inferred state

`ExplicitMemory` stores a student-provided key/value, original quote or message
reference, timestamp and `origin="explicit"`. `history()` is append-only;
`current()` selects the latest fact per key by event time, with insertion-order ties.
It does not silently rewrite profile metadata: application callers decide whether a
confirmed preference should also update the profile.

The existing profile remains student-managed metadata. Old profile fields have no
per-field provenance. Legacy events have empty metadata and **unknown origin**;
they are never relabeled explicit or converted to numerical mastery during migration.
The original `StudentMemory` view still derives Phase 1 topic flags from its original
four event kinds, independently of the new structured state.

Structured evidence distinguishes:

- `explicit`: student statements or explicit interactions;
- `assessment`: a scored answer with an assessment reference;
- `inferred`: a detector's inference with its source/version.

A confusion report sets `reported_status="confused"`, not numerical mastery and not
an inferred “struggling” conclusion. An inferred `LearningState` has evidence IDs
leading back to the original log and its source/origin metadata. No model-generated
claim becomes a fact automatically; write tools require trusted caller confirmation.

## Structured events

The existing `LearningEvent` and `EventKind` are extended, not replaced. `topic` in the
persisted legacy representation carries the stable concept ID for structured events.
New `EvidenceRepository.record()` accepts `(student_id, kind, course_id, concept_id,
evidence, event_id=None, at=None)`.

Supported structured kinds:

- `concept_viewed`, `concept_reviewed`, `explanation_requested`, `lesson_completed`
- `question_answered` with a finite score in `[0, 1]` and a nonempty `attempt_id`
- `misconception_detected`, `misconception_resolved` with a misconception identifier/text
- `student_expressed_confusion`, `student_expressed_confidence` with explicit origin

Correct/incorrect/partial are encoded as **one scored answer event** (1 / 0 / between
0 and 1), avoiding double counting separate answer and outcome events. No lesson or
question content is generated. Future event kinds need validation, reducer rules and
tests; accepting arbitrary strings would undermine the evidence contract.

All evidence requires source metadata. Scored answers require `origin="assessment"`.
The repository does not grade answers or verify a supplied assessment reference;
the trusted caller is responsible for its validity. The Phase 1 `record_event()`
continues to support its original four kinds; structured kinds must use the evidence
repository so source metadata cannot be silently dropped.

Caller-supplied event IDs make retries safe. Repeating an identical event ID or
`(student, course, concept, attempt_id)` returns the existing event; conflicting data
or timestamps fail without a write. Event IDs cannot be reused by another student.
The attempt retry check and insert share a SQLite write transaction. Different events
without caller retry IDs are new events. Automatic UUIDs/timestamps are conveniences;
use fixed IDs/times for byte-for-byte test fixtures.

## Learning state policy `evidence-v1`

State is rebuilt from structured events ordered by occurrence time and insertion
order. There is no mutable mastery table or arbitrary mastery setter. This avoids
stale caches and makes backdated evidence/replay deterministic.

Each `(student, course, concept)` state includes:

- mastery (`None` until scored evidence), confidence, attempts and correct/incorrect/partial counts;
- last interaction, last review, next review;
- inferred status and a **separate** reported status;
- active misconceptions, evidence IDs, origin and policy version.

Transparent initial heuristics (not validated educational/psychometric metrics):

- Mastery = arithmetic mean of scored attempts.
- Confidence = `attempts / (attempts + 5)`; this measures evidence volume, not a
  calibrated probability and not the student's expressed confidence.
- Fewer than 3 attempts: `insufficient_evidence`. Otherwise mastery < 0.5:
  `struggling`; mastery ≥ 0.8: `mastered`; otherwise `developing`.
- No scored attempts: `unknown`. Explicit reports do not change these scores/statuses.
- A review or scored answer schedules review after 7 days if mastered, otherwise
  1 day. This is a placeholder deterministic scheduling policy, not spaced-repetition
  research or a notification service.
- Misconception detection/resolution adds/removes active entries while preserving
  the full log. Sources remain accessible via evidence IDs.

`get_difficult_topics` includes inferred struggling states, explicit confusion reports
and active misconceptions, retaining their distinct fields. No trend or persistent-
misconception classifier is claimed. Future policy versions can replace the service
without rewriting evidence. Current replay scans all student evidence; indexes,
pagination and cached projections are future scale work.

Example (caller-provided IDs/metadata, not course teaching content):

```python
from aria.app import Aria
from aria.learning import Evidence
from aria.student_memory import EventKind, StudentProfile

with Aria("data/aria.sqlite3") as app:
    app.profiles.save(StudentProfile("s", "Student"))  # initial creation only
    app.explicit_memory.record("s", "explanation_preference", "diagrams", "Student's own statement")
    app.evidence.record(
        "s", EventKind.STUDENT_EXPRESSED_CONFUSION, "user-course", "user-concept",
        Evidence("explicit", "conversation/message reference"), event_id="report-1",
    )
    print(app.learning.get("s", "user-course", "user-concept"))
```

## Course metadata and provenance

`Course(id, title, syllabus_source_ids=[])` preserves the original constructor.
`Topic` belongs to a course. `Concept` belongs to a topic and has source IDs,
prerequisite IDs, related concept IDs and misconception-concept IDs. Relations stay
within the course and must exist before saving; create independent records first,
then add relationships. Direct self-links are rejected; general graph-cycle detection
is not yet implemented.

`CourseDocument` stores ID, course, title, local path, media type and optional hash.
`SourceReference` points to an existing document in the same course and supports
1-based page/slide, section, source text and a local source-image reference. It needs
at least one locator/excerpt. A concept requires at least one valid source reference.
Syllabus content is represented by source references, not an invented syllabus.

Document/source records are immutable after creation (exact retries allowed); new
revisions require new IDs, preserving earlier provenance. Metadata persistence does
not read/check file contents or calculate hashes. Paths are stored references only,
not permissions to read a file. Future ingestion must add source validation, hashing,
path containment, file limits and graph validation. Course titles/topics are metadata;
actual concept/source content is supplied only by callers, never bundled by ARIA.

This is **not** ingestion, parsing, embeddings, search, RAG or a fake retriever.
Course model persistence is available directly; model-facing course knowledge tools
remain explicitly unavailable until a grounded knowledge service is wired.

## Tool contracts

`Tool` retains its positional Phase 1 fields and adds input/output schemas and student
scope. Contracts expose classification (`read`/`write`), confirmation requirement,
availability, description and schema. Existing timetable names remain; aliases
`get_today_classes` and `get_tomorrow_classes` delegate to the same timetable service.

New active tools: `get_mastery`, `get_difficult_topics`, `record_evidence`,
`update_mastery`, `record_misconception`. `update_mastery` appends scored evidence then
replays state; it cannot set an arbitrary numerical value. `record_learning_event`
supports both the original arguments and structured evidence arguments. New structured
writes require `kind`, `course_id`, `concept_id`, `event_id`, `evidence`. See
`EVIDENCE_INPUT` or `describe_tools()` for optional evidence fields.

Reserved contracts: `search_course`, `get_concept`, `get_course_source`, plus all
Phase 1 reserved capabilities. Unavailable tools return `unavailable` before argument
validation; no fake empty-success result or invented source is returned.

The dependency-free validator supports a deliberate JSON Schema subset:
`type`, `properties`, `required`, `additionalProperties`, `items`, `maxItems`, `anyOf`,
`enum`, `minLength`, `minimum`, `maximum`. It is not a general JSON Schema engine;
use only these keywords in adapters. Booleans are not numbers; nonfinite values are
rejected. String `minLength` measures stripped content. Output contracts vary in detail:
state/evidence have required fields, while legacy outputs retain broader object/array
shapes for compatibility. Runtime output validation catches adapter shape violations.

Deterministic codes: `unknown_tool`, `unavailable`, `invalid_arguments`,
`confirmation_required`, `domain_error`, `invalid_output`, `execution_error`.
Unexpected handler errors are sanitized, superseding Phase 1's propagation behavior.
A write may have succeeded before an output-contract error; retry only using stable
idempotency keys. Tools are trusted local code; schemas are not a sandbox.

## Context building

`app.context_builder().build_context(student_id, conversation_id, user_message)`
returns a JSON-compatible dictionary, not the database:

- student metadata and explicit preferences/facts;
- recent conversation from only that student/session;
- selected inferred state and recent learning evidence;
- today's/tomorrow's/next timetable occurrence;
- current local time and tool contracts;
- an explicitly unavailable course-knowledge section with no sources.

Defaults: 12 messages, 20 events, 8 concepts, 12 facts, 600 characters per text field,
4,000-character user input and a 48,000-character serialized context ceiling. Nested
lists are capped at 20. Context includes selection limits and truncation markers.
Concept relevance is a case-insensitive concept-ID substring match followed by
recency, **not semantic retrieval**. Explicit facts use stable key ordering; all are
candidates, not a learned relevance policy. History is bounded after local repository
reads; the model never receives the entire log, but local reads are not paginated yet.

One clock snapshot drives context time and timetable calculations, using Phase 1's
local timezone semantics. Inject `clock` and timezone for tests. Oversized input or a
budget too small for mandatory sections fails before conversation writes. Tool schemas
are never truncated. The future adapter must translate character limits into a
model-specific token budget.

## Provider, mock and agent loop

`ModelProvider.generate(ModelInput) → FinalResponse | ToolRequest` and
`stream(ModelInput) → Iterator[ModelAction]` form the new model boundary. Streaming
means complete actions, not partial tool JSON. The synchronous orchestrator currently
uses `generate`; token streaming/UI transport remains future work.

`MockModel` plays caller-supplied actions in order, snapshots requests, supports reset,
and returns explicit unavailable when exhausted. It does not match keywords or
pretend to reason. `examples/phase2_demo.py` shows a scripted timetable request.
A future local adapter owns prompt templates, output parsing and model loading; it
must produce these actions. No provider downloads, cloud fallback or runtime exists.

`Orchestrator.run(student_id, conversation_id, user_message)`:

1. Builds context and binds tools to the student.
2. Records the user message locally.
3. Calls the provider, validates the action, executes the requested registered tool.
4. Records/feeds back the bounded tool result, then lets the model continue.
5. Records a final response or explicit error/limit response.

Default limits: 6 provider calls, 12,000 characters per tool request/result, 8,000
characters per final response. Repeated call IDs cannot execute twice. Oversized tool
results are replaced with an explicit `result_too_large` error. Conversation history
contains tool traces; `AgentResult` exposes the trace and model-call count.

Every write requires `confirm(student_id, ToolRequest)` from the **trusted caller**;
no callback means denied. It must inspect the exact action and obtain real permission;
never use an unconditional approval callback outside controlled tests. Models cannot
set confirmation or change bound student identity. Learning state changes only through
confirmed evidence writes; final prose does not trigger inferred memory extraction.
Automatic conversation logging is part of the caller-invoked chat operation, not an
extra model tool permission.

Provider failures/invalid actions/step limits terminate clearly. If any tool failed,
a later model final is replaced with a deterministic unavailable response; the trace
retains the precise cause. This conservative policy does not yet support nuanced
partial-success recovery. A successful earlier write is not rolled back by a later
failure. The entire conversation is not one transaction. No cross-turn resume or
crash-recovery protocol exists yet.

This boundary cannot prove arbitrary model prose truthful. A future real provider
needs grounding/citation checks and adversarial evaluations; the unavailable-tool
safeguard is not a general hallucination detector.

## Evaluation and tests

`EvaluationCase` defines user input, expected tool sequence/status/new event kinds,
and optional named checks over authoritative results/context. `EvaluationHarness`
returns per-check booleans and aggregate pass rates. Checks can inspect the injected
application's state to evaluate mastery, explicit/inferred separation and memory.
Exceptions yield a failed case, not a fabricated score. These are behavioral tests,
not measurements of teaching quality.

Create a **fresh database/provider per case** or explicitly reset fixtures to avoid
conversation/state leakage. Fixed clocks/scripts are repeatable. The harness accepts
any orchestrator/provider, so future comparisons need no harness rewrite. Current
scripted cases validate infrastructure/tool selection plumbing, not a real model's
ability to choose tools from natural language.

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
TZ=America/New_York PYTHONPATH=src python -m unittest discover -s tests -q
PYTHONPATH=src python -m compileall -q src tests examples
PYTHONPATH=src python examples/local_demo.py
PYTHONPATH=src python examples/phase2_demo.py
```

Phase 1 tests are unchanged. Phase 2 tests cover migration/reopen, isolation, evidence
validation/retries/replay, explicit memory, mastery/reviews/misconceptions, course
relationships/provenance/revisions, schemas, context limits, mock streaming/reset,
confirmation, unavailable tools, errors/limits, result feedback and evaluation cases.
Offline tests disable socket creation. The existing documented CI template discovers
all tests; workflow activation still needs GitHub workflow-write permission.

## Intentionally unavailable and recommended next phase

No PDF/PPTX/DOCX parsing, embeddings, vector database, RAG, local LLM inference,
fine-tuning, voice, vision, image generation or real course tutoring. No course
material is invented. No dependencies or cloud AI APIs were added.

Next: review the event/mastery policy and contracts; add a user-confirmed local
inference adapter once a model is available, with token budgets, parsed-action
validation and adversarial grounding evaluations. When real slides arrive, separately
implement validated local ingestion/provenance and only then enable course knowledge
tools. Keep assessment policy validation, consent, privacy and real-model evaluation
explicit rather than treating scripted mock test success as tutor readiness.
