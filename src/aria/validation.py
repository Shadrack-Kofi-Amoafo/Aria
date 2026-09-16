"""Small shared domain validators."""
from datetime import datetime


def nonempty(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def strings(values: list[str], field: str) -> None:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list")
    for value in values:
        nonempty(value, field)


def aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
