from __future__ import annotations

from typing import Protocol


class IssuePort(Protocol):
    def create(self, title: str, body: str) -> str: ...

    def update(self, issue_id: str, body: str) -> None: ...

    def link(self, issue_id: str, artifact: str) -> None: ...


class CIPort(Protocol):
    def trigger(self, ref: str, env: str) -> str: ...

    def status(self, pipeline_id: str) -> str: ...


class NotifyPort(Protocol):
    def send(self, event: str, body: str) -> None: ...


class SecretPort(Protocol):
    def resolve(self, name: str) -> str: ...

    def validate(self, names: list[str]) -> list[str]: ...


class ObservePort(Protocol):
    def health(self, url: str) -> tuple[bool, str]: ...
