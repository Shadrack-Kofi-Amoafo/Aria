"""Offline orchestration/evaluation demo. All model actions are explicitly scripted."""
from pprint import pprint
from aria.app import Aria
from aria.evaluation import EvaluationCase, EvaluationHarness
from aria.model import FinalResponse, MockModel, ToolRequest
from aria.student_memory import StudentProfile

with Aria(":memory:") as app:
    app.profiles.save(StudentProfile("demo", "Demo Student"))
    model = MockModel([
        ToolRequest("get_tomorrow_classes", call_id="schedule-1"),
        FinalResponse("Script complete. Inspect the authoritative timetable result in the trace."),
    ])
    agent = app.orchestrator(model)
    case = EvaluationCase("tomorrow_tool", "demo", "demo-session", "What are tomorrow's classes?",
                          expected_tools=("get_tomorrow_classes",))
    result = EvaluationHarness().run_case(case, agent, app.evidence)
    pprint(result)
    pprint(model.requests[-1].tool_results)
