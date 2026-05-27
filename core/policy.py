"""
Policy primitives for PulseRelay.

The policy layer decides whether a proposed route is allowed, blocked, or
requires human approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from core.event import EventEnvelope, RiskLevel

PolicyStatus = Literal["allowed", "blocked", "requires_approval"]

RISK_ORDER: dict[RiskLevel, int] = {
    "read": 0,
    "write": 1,
    "deploy": 2,
    "payment": 3,
    "admin": 4,
}


@dataclass
class PolicyDecision:
    """Decision returned by the policy layer."""

    id: str = field(default_factory=lambda: f"pol_{uuid4().hex}")
    status: PolicyStatus = "allowed"
    reason: str = ""
    requires_approval: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.status in {"allowed", "requires_approval"}


@dataclass
class PolicyContext:
    """Runtime context available to policies."""

    requested_risk_level: RiskLevel = "read"
    requested_tools: list[str] = field(default_factory=list)
    requested_agents: list[str] = field(default_factory=list)
    requested_deliveries: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class PolicyEngine:
    """
    Minimal deterministic policy engine.

    This is intentionally conservative and dependency-free. AI-assisted policy
    checks can be layered on top later.
    """

    def evaluate(self, event: EventEnvelope, context: PolicyContext | None = None) -> PolicyDecision:
        context = context or PolicyContext()

        max_risk = event.permissions.max_risk_level
        if RISK_ORDER[context.requested_risk_level] > RISK_ORDER[max_risk]:
            return PolicyDecision(
                status="requires_approval" if event.permissions.requires_approval else "blocked",
                requires_approval=event.permissions.requires_approval,
                reason=f"requested risk {context.requested_risk_level} exceeds max risk {max_risk}",
                metadata={"requested_risk_level": context.requested_risk_level, "max_risk_level": max_risk},
            )

        if event.permissions.allowed_tools:
            denied_tools = [tool for tool in context.requested_tools if tool not in event.permissions.allowed_tools]
            if denied_tools:
                return PolicyDecision(
                    status="blocked",
                    reason="requested tools are not allowed",
                    metadata={"denied_tools": denied_tools},
                )

        if event.permissions.allowed_agents:
            denied_agents = [agent for agent in context.requested_agents if agent not in event.permissions.allowed_agents]
            if denied_agents:
                return PolicyDecision(
                    status="blocked",
                    reason="requested agents are not allowed",
                    metadata={"denied_agents": denied_agents},
                )

        if event.permissions.requires_approval:
            return PolicyDecision(
                status="requires_approval",
                requires_approval=True,
                reason="event requires approval",
            )

        return PolicyDecision(status="allowed", reason="policy passed")
