"""Composition root. Replace adapters here without changing domain consumers."""
from pathlib import Path
from aria.agent import ToolAgent, build_agent
from aria.storage import Database
from aria.student_memory import SQLiteMemoryRepository, SQLiteProfileRepository, StudentMemory
from aria.timetable import SQLiteScheduleRepository, Timetable


class Aria:
    def __init__(self, database_path: str | Path = "data/aria.sqlite3") -> None:
        self.db = Database(database_path)
        self.profiles = SQLiteProfileRepository(self.db)
        self.memory = SQLiteMemoryRepository(self.db)
        self.student_memory = StudentMemory(self.memory)
        self.schedule = SQLiteScheduleRepository(self.db)
        self.timetable = Timetable(self.schedule)

    def agent_for(self, student_id: str) -> ToolAgent:
        return build_agent(student_id, self.profiles, self.memory, self.timetable)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Aria":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
