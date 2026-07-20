"""Crash recovery queries with no mutation until the user finishes recovery."""

from voxtype.sessions.repository import SessionRecord, SessionRepository


def recoverable_sessions(repository: SessionRepository) -> tuple[SessionRecord, ...]:
    return repository.unfinished()


def mark_recovered(repository: SessionRepository, session_id: str) -> None:
    repository.finish(session_id, status="recovered")
