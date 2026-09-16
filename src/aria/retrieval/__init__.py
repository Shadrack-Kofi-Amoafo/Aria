"""Future retrieval port. No embedding model, vector store, or RAG implemented."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SlideExcerpt:
    course_id: str
    source_path: str
    page: int
    text: str


class SlideRetriever(Protocol):
    def retrieve(self, course_id: str, query: str, limit: int = 5) -> list[SlideExcerpt]: ...
