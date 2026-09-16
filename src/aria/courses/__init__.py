"""Future local course catalog and ingestion boundary; no content bundled."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Course:
    id: str
    title: str


class CourseCatalog(Protocol):
    def search(self, query: str) -> list[Course]: ...


class CourseIngestor(Protocol):
    def ingest(self, course_id: str, source: Path) -> None:
        """Future adapter: parse a local source and retain page/slide provenance."""
        ...
