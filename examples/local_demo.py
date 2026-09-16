"""Run with PYTHONPATH=src python examples/local_demo.py (no persistent demo data)."""
from datetime import datetime, time
from pprint import pprint
from aria.app import Aria
from aria.student_memory import StudentProfile
from aria.timetable import WeeklyClass

with Aria(":memory:") as aria:
    # User-supplied metadata only, not course teaching content.
    aria.profiles.save(StudentProfile("demo", "Demo Student", preferred_name="Student"))
    aria.schedule.save(WeeklyClass("demo-class", "demo", "user-course", "My class",
                                   datetime.now().weekday(), time(9), time(10)))
    agent = aria.agent_for("demo")
    pprint(agent.execute("get_student_profile"))
    pprint(agent.execute("get_todays_classes"))
    pprint(agent.execute("slide_retrieval", {"course_id": "user-course", "query": "topic"}))
