"""Composition root. Replace adapters here without changing domain consumers."""
from pathlib import Path
from aria.agent import ToolAgent, build_agent
from aria.storage import Database
from aria.student_memory import SQLiteMemoryRepository, SQLiteProfileRepository, StudentMemory
from aria.courses import SQLiteCourseRepository
from aria.learning import SQLiteEvidenceRepository, LearningStateService
from aria.student_memory.explicit import SQLiteExplicitMemoryRepository
from aria.agent.learning_tools import extend_agent
from aria.timetable import SQLiteScheduleRepository, Timetable


class Aria:
    def __init__(self, database_path: str | Path = "data/aria.sqlite3") -> None:
        self.db = Database(database_path)
        self.profiles = SQLiteProfileRepository(self.db)
        self.memory = SQLiteMemoryRepository(self.db)
        self.student_memory = StudentMemory(self.memory)
        self.explicit_memory = SQLiteExplicitMemoryRepository(self.db)
        self.evidence = SQLiteEvidenceRepository(self.memory)
        self.learning = LearningStateService(self.evidence)
        self.courses = SQLiteCourseRepository(self.db)
        self.schedule = SQLiteScheduleRepository(self.db)
        self.timetable = Timetable(self.schedule)

    def agent_for(self, student_id: str) -> ToolAgent:
        return extend_agent(build_agent(student_id, self.profiles, self.memory, self.timetable),
                            student_id, self.evidence, self.learning)

    def context_builder(self, **options):
        from aria.context import ContextBuilder
        return ContextBuilder(self.profiles, self.memory, self.explicit_memory, self.learning,
                              self.timetable, self.agent_for, **options)

    def orchestrator(self, provider, **options):
        from aria.agent.orchestrator import Orchestrator
        return Orchestrator(self.context_builder(), self.agent_for, provider, self.memory, **options)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Aria":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
