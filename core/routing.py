"""Pure route matching and JSON-path-like field access."""
from __future__ import annotations

from typing import Any

from core.event import EventEnvelope
from core.persistence import RouteRecord


def get_field(event: EventEnvelope, path: str) -> Any:
    value: Any = event.to_dict()
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def matches(event: EventEnvelope, rule: dict[str, Any]) -> bool:
    for path, expected in (rule or {}).items():
        actual = get_field(event, path)
        if isinstance(expected, list):
            if isinstance(actual, list):
                if not set(expected).intersection(actual):
                    return False
            elif actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def matching_routes(event: EventEnvelope, routes: list[RouteRecord]) -> list[RouteRecord]:
    return [route for route in routes if route.enabled and matches(event, route.match)]
