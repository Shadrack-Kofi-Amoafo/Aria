"""Phase 2 contracts layered over the unchanged Phase 1 tool names."""
from dataclasses import replace
from aria.agent import Tool
from aria.agent.schema import object_schema
from aria.learning import Evidence
from aria.student_memory import EventKind

TEXT = {"type": "string", "minLength": 1}
STATE = {"type": "object", "required": ["student_id", "course_id", "concept_id", "mastery", "origin", "evidence_ids"],
         "properties": {"student_id": TEXT, "course_id": TEXT, "concept_id": TEXT,
                        "mastery": {"anyOf": [{"type": "null"}, {"type": "number", "minimum": 0, "maximum": 1}]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "attempts": {"type": "integer", "minimum": 0},
                        "origin": {"type": "string", "enum": ["inferred"]},
                        "evidence_ids": {"type": "array", "items": TEXT}}}
EVENT = {"type": "object", "required": ["id", "student_id", "kind", "course_id", "topic", "metadata"]}
EVIDENCE_INPUT = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": [e.value for e in EventKind if e not in {EventKind.MASTERY, EventKind.DIFFICULTY, EventKind.MISCONCEPTION, EventKind.STUDY}]},
        "course_id": TEXT, "concept_id": TEXT, "event_id": TEXT,
        "evidence": {"type": "object", "properties": {
            "origin": {"type": "string", "enum": ["explicit", "assessment", "inferred"]},
            "source": TEXT, "score": {"type": "number", "minimum": 0, "maximum": 1},
            "attempt_id": TEXT, "misconception": TEXT},
            "required": ["origin", "source"], "additionalProperties": False}},
    "required": ["kind", "course_id", "concept_id", "event_id", "evidence"],
    "additionalProperties": False}


def extend_agent(agent, student_id, evidence, learning):
    # Attach output contracts to legacy tools without changing their signatures.
    arrays = {"get_learning_history", "get_conversations", "get_todays_classes", "get_tomorrows_classes",
              "course_search", "slide_retrieval", "create_quiz"}
    for tool in agent.tools():
        name = tool.name
        output = {"type": "array", "items": {"type": "object"}} if name in arrays else {"type": "object"}
        if name == "get_next_class":
            output = {"anyOf": [{"type": "object"}, {"type": "null"}]}
        agent.replace(replace(tool, output_schema=output))

    def record(kind, course_id, concept_id, event_id, evidence):
        return evidence_repository.record(student_id, EventKind(kind), course_id, concept_id,
                                          Evidence(**evidence), event_id=event_id)

    evidence_repository = evidence
    legacy = agent.get_tool("record_learning_event")

    def compatible_record(**args):
        return legacy.handler(**args) if "topic" in args else record(**args)

    agent.replace(replace(legacy, handler=compatible_record,
                          input_schema={"anyOf": [object_schema(legacy.parameters), EVIDENCE_INPUT]}, output_schema=EVENT))
    agent.register(Tool("record_evidence", "Record structured learning evidence, not unverified model assertions", {},
                        record, True, EVIDENCE_INPUT, EVENT))
    agent.register(Tool("get_mastery", "Read inferred evidence-based concept state", {"course_id": str, "concept_id": str},
                        lambda course_id, concept_id: learning.get(student_id, course_id, concept_id), output_schema=STATE))
    agent.register(Tool("get_difficult_topics", "Read inferred difficulties and separately labeled student reports", {},
                        lambda: learning.difficult_topics(student_id), output_schema={"type": "array", "items": STATE}))
    for alias, original in (("get_today_classes", "get_todays_classes"), ("get_tomorrow_classes", "get_tomorrows_classes")):
        agent.register(replace(agent.get_tool(original), name=alias))

    def update_mastery(**args):
        if args["kind"] != EventKind.QUESTION_ANSWERED.value:
            raise ValueError("update_mastery requires scored question_answered evidence")
        record(**args)
        return learning.get(student_id, args["course_id"], args["concept_id"])

    def misconception(**args):
        if args["kind"] not in {EventKind.MISCONCEPTION_DETECTED.value, EventKind.MISCONCEPTION_RESOLVED.value}:
            raise ValueError("Expected misconception evidence")
        return record(**args)

    agent.register(Tool("update_mastery", "Append scored evidence and return recomputed state; no manual score setter", {},
                        update_mastery, True, EVIDENCE_INPUT, STATE))
    agent.register(Tool("record_misconception", "Record detection/resolution with provenance", {},
                        misconception, True, EVIDENCE_INPUT, EVENT))
    for name, params, output in (
        ("search_course", {"query": str}, {"type": "array", "items": {"type": "object"}}),
        ("get_concept", {"course_id": str, "concept_id": str}, {"type": "object"}),
        ("get_course_source", {"course_id": str, "source_id": str}, {"type": "object"}),
    ):
        agent.register(Tool(name, "Unavailable: course knowledge service not configured", params, output_schema=output))
    return agent
