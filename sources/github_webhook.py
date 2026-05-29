"""
GitHub webhook source adapter.

Receives GitHub webhook POST requests, normalizes them into EventEnvelope,
and publishes events into the EventBus.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from core.event import EventEnvelope, EventSource, EventSender, EventMeta, EventContent, EventContext, EventPermissions
from core.source_adapter import SourceAdapter, SourceManifest, SourceCapabilities

logger = logging.getLogger(__name__)


class GitHubWebhookSource(SourceAdapter):
    """
    Source adapter for GitHub webhooks.

    Handles GitHub webhook events and normalizes them into EventEnvelope objects.
    """

    manifest = SourceManifest(
        id="github_webhook",
        name="GitHub Webhook",
        version="0.1.0",
        description="Receives and processes GitHub webhook events",
        capabilities=SourceCapabilities(supports_webhook=True),
        tags=["github", "webhook", "ci/cd"],
    )

    def __init__(self, event_bus, enabled: bool = True, webhook_secret: str = ""):
        super().__init__(event_bus, enabled)
        self.webhook_secret = webhook_secret

    def verify_signature(self, payload: bytes, signature: str | None) -> bool:
        """
        Verify that the webhook payload signature matches the expected HMAC-SHA256.

        GitHub sends the signature in the 'X-Hub-Signature-256' header as 'sha256=<signature>'.
        """
        if not self.webhook_secret or not signature:
            return True  # Skip verification if no secret configured

        if not signature.startswith("sha256="):
            return False

        expected_signature = "sha256=" + hmac.new(
            self.webhook_secret.encode("utf-8"),
            payload,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(signature, expected_signature)

    def _map_github_event(self, event_type: str, payload: dict[str, Any]) -> EventEnvelope | None:
        """
        Map GitHub webhook event to EventEnvelope based on event type.

        Args:
            event_type: The GitHub event type (e.g., 'push', 'pull_request', 'issues')
            payload: The parsed JSON payload from GitHub

        Returns:
            EventEnvelope or None if event should be ignored
        """
        repo = payload.get("repository", {})
        repo_name = repo.get("full_name", "unknown")
        repo_url = repo.get("html_url", "")

        sender = payload.get("sender", {})
        sender_id = sender.get("login", "unknown")
        sender_name = sender.get("login", "unknown")

        # Map event type to internal event structure
        if event_type == "push":
            return self._map_push_event(payload, repo_name, repo_url, sender_id, sender_name)
        elif event_type == "pull_request":
            return self._map_pull_request_event(payload, repo_name, repo_url, sender_id, sender_name)
        elif event_type == "issues":
            return self._map_issues_event(payload, repo_name, repo_url, sender_id, sender_name)
        elif event_type == "issue_comment":
            return self._map_issue_comment_event(payload, repo_name, repo_url, sender_id, sender_name)
        elif event_type == "create":
            return self._map_create_event(payload, repo_name, repo_url, sender_id, sender_name)
        elif event_type == "delete":
            return self._map_delete_event(payload, repo_name, repo_url, sender_id, sender_name)
        elif event_type == "release":
            return self._map_release_event(payload, repo_name, repo_url, sender_id, sender_name)
        else:
            logger.info(f"Unhandled GitHub event type: {event_type}")
            return None

    def _map_push_event(self, payload: dict[str, Any], repo_name: str, repo_url: str, sender_id: str, sender_name: str) -> EventEnvelope:
        """Map GitHub push event to EventEnvelope."""
        ref = payload.get("ref", "")
        commits = payload.get("commits", [])
        head_commit = payload.get("head_commit", {})

        # Build commit summary
        commit_count = len(commits)
        commit_messages = [c.get("message", "").split("\n")[0] for c in commits[:5]]
        commit_summary = ", ".join(commit_messages) if commit_messages else head_commit.get("message", "")

        content = EventContent(
            title=f"Pushed to {ref}",
            text=commit_summary,
            raw=payload,
        )

        context = EventContext(
            repo=repo_name,
            url=repo_url,
            extra={
                "ref": ref,
                "commits_count": commit_count,
                "head_commit_id": head_commit.get("id", ""),
            },
        )

        return EventEnvelope(
            source=EventSource(type="github", id=repo_name, name="GitHub"),
            sender=EventSender(id=sender_id, name=sender_name, trust_level="system"),
            event=EventMeta(
                type="github.push",
                action="push",
                dedupe_key=f"github:{repo_name}:push:{payload.get('after', '')}",
            ),
            content=content,
            context=context,
            permissions=EventPermissions(max_risk_level="deploy"),
        )

    def _map_pull_request_event(self, payload: dict[str, Any], repo_name: str, repo_url: str, sender_id: str, sender_name: str) -> EventEnvelope:
        """Map GitHub pull_request event to EventEnvelope."""
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number", 0)
        pr_title = pr.get("title", "")
        pr_state = pr.get("state", "")
        pr_url = pr.get("html_url", "")
        head_branch = pr.get("head", {}).get("ref", "")
        base_branch = pr.get("base", {}).get("ref", "")

        content = EventContent(
            title=f"Pull Request #{pr_number}: {pr_title}",
            text=f"{action.upper()} - {pr_state} - {head_branch} -> {base_branch}",
            raw=payload,
        )

        context = EventContext(
            repo=repo_name,
            url=pr_url,
            thread_id=str(pr_number),
            extra={
                "pr_number": pr_number,
                "action": action,
                "state": pr_state,
                "head_branch": head_branch,
                "base_branch": base_branch,
            },
        )

        return EventEnvelope(
            source=EventSource(type="github", id=repo_name, name="GitHub"),
            sender=EventSender(id=sender_id, name=sender_name, trust_level="user"),
            event=EventMeta(
                type="github.pull_request",
                action=action,
                dedupe_key=f"github:{repo_name}:pr:{pr_number}:{action}",
            ),
            content=content,
            context=context,
            permissions=EventPermissions(max_risk_level="write"),
        )

    def _map_issues_event(self, payload: dict[str, Any], repo_name: str, repo_url: str, sender_id: str, sender_name: str) -> EventEnvelope:
        """Map GitHub issues event to EventEnvelope."""
        action = payload.get("action", "")
        issue = payload.get("issue", {})
        issue_number = issue.get("number", 0)
        issue_title = issue.get("title", "")
        issue_body = issue.get("body", "") or ""
        issue_url = issue.get("html_url", "")
        labels = [l.get("name", "") for l in issue.get("labels", [])]

        content = EventContent(
            title=f"Issue #{issue_number}: {issue_title}",
            text=f"{action.upper()}: {issue_body[:500]}",
            raw=payload,
        )

        context = EventContext(
            repo=repo_name,
            url=issue_url,
            thread_id=str(issue_number),
            extra={
                "issue_number": issue_number,
                "action": action,
                "labels": labels,
            },
        )

        return EventEnvelope(
            source=EventSource(type="github", id=repo_name, name="GitHub"),
            sender=EventSender(id=sender_id, name=sender_name, trust_level="user"),
            event=EventMeta(
                type="github.issues",
                action=action,
                dedupe_key=f"github:{repo_name}:issue:{issue_number}:{action}",
            ),
            content=content,
            context=context,
            permissions=EventPermissions(max_risk_level="write"),
        )

    def _map_issue_comment_event(self, payload: dict[str, Any], repo_name: str, repo_url: str, sender_id: str, sender_name: str) -> EventEnvelope:
        """Map GitHub issue_comment event to EventEnvelope."""
        action = payload.get("action", "")
        comment = payload.get("comment", {})
        issue = payload.get("issue", {})
        issue_number = issue.get("number", 0)
        comment_body = comment.get("body", "") or ""
        comment_url = comment.get("html_url", "")

        content = EventContent(
            title=f"Comment on Issue #{issue_number}",
            text=comment_body[:500],
            raw=payload,
        )

        context = EventContext(
            repo=repo_name,
            url=comment_url,
            thread_id=str(issue_number),
            extra={
                "issue_number": issue_number,
                "action": action,
            },
        )

        return EventEnvelope(
            source=EventSource(type="github", id=repo_name, name="GitHub"),
            sender=EventSender(id=sender_id, name=sender_name, trust_level="user"),
            event=EventMeta(
                type="github.issue_comment",
                action=action,
                dedupe_key=f"github:{repo_name}:issue_comment:{comment.get('id', '')}",
            ),
            content=content,
            context=context,
            permissions=EventPermissions(max_risk_level="write"),
        )

    def _map_create_event(self, payload: dict[str, Any], repo_name: str, repo_url: str, sender_id: str, sender_name: str) -> EventEnvelope:
        """Map GitHub create event (branch/tag creation) to EventEnvelope."""
        ref_type = payload.get("ref_type", "")
        ref = payload.get("ref", "")

        content = EventContent(
            title=f"Created {ref_type}",
            text=f"Created {ref_type} '{ref}'",
            raw=payload,
        )

        context = EventContext(
            repo=repo_name,
            url=repo_url,
            extra={
                "ref_type": ref_type,
                "ref": ref,
            },
        )

        return EventEnvelope(
            source=EventSource(type="github", id=repo_name, name="GitHub"),
            sender=EventSender(id=sender_id, name=sender_name, trust_level="user"),
            event=EventMeta(
                type="github.create",
                action="created",
                dedupe_key=f"github:{repo_name}:create:{ref_type}:{ref}",
            ),
            content=content,
            context=context,
            permissions=EventPermissions(max_risk_level="write"),
        )

    def _map_delete_event(self, payload: dict[str, Any], repo_name: str, repo_url: str, sender_id: str, sender_name: str) -> EventEnvelope:
        """Map GitHub delete event (branch/tag deletion) to EventEnvelope."""
        ref_type = payload.get("ref_type", "")
        ref = payload.get("ref", "")

        content = EventContent(
            title=f"Deleted {ref_type}",
            text=f"Deleted {ref_type} '{ref}'",
            raw=payload,
        )

        context = EventContext(
            repo=repo_name,
            url=repo_url,
            extra={
                "ref_type": ref_type,
                "ref": ref,
            },
        )

        return EventEnvelope(
            source=EventSource(type="github", id=repo_name, name="GitHub"),
            sender=EventSender(id=sender_id, name=sender_name, trust_level="user"),
            event=EventMeta(
                type="github.delete",
                action="deleted",
                dedupe_key=f"github:{repo_name}:delete:{ref_type}:{ref}",
            ),
            content=content,
            context=context,
            permissions=EventPermissions(max_risk_level="write"),
        )

    def _map_release_event(self, payload: dict[str, Any], repo_name: str, repo_url: str, sender_id: str, sender_name: str) -> EventEnvelope:
        """Map GitHub release event to EventEnvelope."""
        action = payload.get("action", "")
        release = payload.get("release", {})
        tag_name = release.get("tag_name", "")
        release_name = release.get("name", "") or tag_name
        release_body = release.get("body", "") or ""

        content = EventContent(
            title=f"Release {release_name}",
            text=f"{action.upper()}: {release_body[:500]}",
            raw=payload,
        )

        context = EventContext(
            repo=repo_name,
            url=release.get("html_url", ""),
            extra={
                "tag_name": tag_name,
                "action": action,
            },
        )

        return EventEnvelope(
            source=EventSource(type="github", id=repo_name, name="GitHub"),
            sender=EventSender(id=sender_id, name=sender_name, trust_level="system"),
            event=EventMeta(
                type="github.release",
                action=action,
                dedupe_key=f"github:{repo_name}:release:{tag_name}",
            ),
            content=content,
            context=context,
            permissions=EventPermissions(max_risk_level="deploy"),
        )

    async def on_webhook(self, payload: dict[str, Any], headers: dict[str, str], raw_body: bytes | None = None) -> EventEnvelope | None:
        """
        Process an incoming GitHub webhook.

        Args:
            payload: Parsed JSON payload from GitHub
            headers: HTTP headers from the webhook request
            raw_body: Raw request body for signature verification

        Returns:
            EventEnvelope if the event was processed, None if ignored
        """
        event_type = headers.get("X-GitHub-Event", "")
        delivery_id = headers.get("X-GitHub-Delivery", "")

        logger.info(f"GitHub webhook received: event={event_type}, delivery={delivery_id}")

        # Verify signature if raw body provided and secret configured
        if raw_body:
            signature = headers.get("X-Hub-Signature-256")
            if not self.verify_signature(raw_body, signature):
                logger.warning(f"GitHub webhook signature verification failed for delivery {delivery_id}")
                return None

        # Map to EventEnvelope
        event = self._map_github_event(event_type, payload)
        if event:
            event.context.extra["github_delivery_id"] = delivery_id
            event.context.extra["github_event"] = event_type
            await self.emit(event)
            logger.info(f"GitHub webhook event processed: {event.event.type}")
        else:
            logger.debug(f"GitHub webhook event ignored: {event_type}")

        return event

    async def run(self):
        """Main event loop for the source adapter.

        GitHub webhooks are received via HTTP, so this is a no-op.
        The actual webhook handling happens in on_webhook().
        """
        # GitHub webhooks are received via HTTP, not in a loop
        # Keep the adapter alive
        import asyncio
        while True:
            await asyncio.sleep(3600)  # Sleep in 1-hour intervals
