"""
Router primitives for PulseRelay.

The router is responsible for deciding what should happen next after an event
is received and normalized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from core.event import EventEnvelope, RiskLevel
from core.policy import PolicyContext, PolicyDecision, PolicyEngine


@dataclass
class RouteTarget:
    """A downstream target selected by the router."""

    type: str
    id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteDecision:
    """Structured routing decision."""

    id: str = field(default_factory=lambda: f"route_{uuid4().hex}")
    accepted: bool = True
    reason: str = ""

    target_agent: str = ""
    target_delivery: str = ""

    risk_level: RiskLevel = "read"
    requires_approval: bool = False

    labels: list[str] = field(default_factory=list)
    targets: list[RouteTarget] = field(default_factory=list)

    policy: PolicyDecision | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Router:
    """
    Deterministic routing layer.

    Current responsibilities:

    - classify events using simple rules
    - assign risk levels
    - choose default deliveries
    - invoke policy checks

    Future versions may integrate:

    - AI classification
    - capability matching
    - runtime scheduling
    - approval workflows
    - memory/context retrieval
    """

    def __init__(self, policy_engine: PolicyEngine | None = None):
        self.policy_engine = policy_engine or PolicyEngine()

    def classify(self, event: EventEnvelope) -> tuple[list[str], RiskLevel]:
        """Very lightweight deterministic classifier."""

        text = (event.content.text or "").lower()
        labels: list[str] = []
        risk: RiskLevel = "read"

        if any(keyword in text for keyword in ["error", "failed", "exception", "timeout"]):
            labels.append("incident")
            risk = "write"

        if any(keyword in text for keyword in ["deploy", "production", "release"]):
            labels.append("deployment")
            risk = "deploy"

        if any(keyword in text for keyword in ["payment", "invoice", "refund"]):
            labels.append("payment")
            risk = "payment"

        if event.source.type in {"github", "gitlab"}:
            labels.append("code")

        if not labels:
            labels.append("general")

        return labels, risk

    def route(self, event: EventEnvelope) -> RouteDecision:
        """Produce a routing decision for one event."""

        labels, risk = self.classify(event)

        target_delivery = "bark"
        target_agent = ""

        if "incident" in labels:
            target_delivery = "feishu"

        if "code" in labels:
            target_agent = "claude-code"

        if "deployment" in labels:
            target_agent = "deployment-reviewer"

        policy_context = PolicyContext(
            requested_risk_level=risk,
            requested_agents=[target_agent] if target_agent else [],
            requested_deliveries=[target_delivery],
        )

        policy = self.policy_engine.evaluate(event, policy_context)

        accepted = policy.allowed

        decision = RouteDecision(
            accepted=accepted,
            reason=policy.reason,
            target_agent=target_agent,
            target_delivery=target_delivery,
            risk_level=risk,
            requires_approval=policy.requires_approval,
            labels=labels,
            policy=policy,
        )

        if target_delivery:
            decision.targets.append(RouteTarget(type="delivery", id=target_delivery))

        if target_agent:
            decision.targets.append(RouteTarget(type="agent", id=target_agent))

        return decision
