"""A post's full history, from idea to approval and publishing, in time order.

Pulled from the records each module already keeps: the planning record (where
the idea came from), audit events about the post (production stages, shoots,
reopened approvals), versions, approval records, comments (with pins) and the
publish log. Internal comments and notes are left out when ``include_internal``
is False (anything shown to a client).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Event:
    at: datetime
    kind: (
        str  # idea, plan, production, shoot, version, submitted, approved, changes, reopened, comment, publish, created
    )
    title: str
    detail: str = ""
    actor: str = ""
    tone: str = "neutral"
    internal: bool = False
    extra: dict = field(default_factory=dict)


def _name(user) -> str:
    return (user.name or user.email) if user else ""


def post_history(post, *, include_internal=True) -> list[Event]:
    from apps.approvals.models import PostComment
    from apps.audit.models import AuditEvent
    from apps.composer.models import PostVersion
    from apps.publisher.models import PublishLog
    from apps.reviews.models import ApprovalRecord

    events: list[Event] = [Event(post.created_at, "created", "Post created", actor=_name(post.author))]

    try:
        idea = post.source_idea  # BrightBean's Idea.post link, set when an idea becomes a post
    except Exception:  # noqa: BLE001 - not from an idea
        idea = None
    if idea is not None:
        events.append(
            Event(
                idea.created_at,
                "idea",
                f"Idea: {idea.title or 'untitled'}",
                actor=_name(idea.author),
                detail=(idea.description or "")[:200],
            )
        )

    for event in (
        AuditEvent.objects.filter(target_type="Post", target_id=str(post.pk))
        .exclude(verb__startswith="approval.")  # approval records below say the same, with the exact version
        .select_related("actor")
        .order_by("created_at")
    ):
        kind = event.verb.split(".")[0]
        events.append(
            Event(
                event.created_at,
                kind if kind in ("production", "shoot", "planning") else "note",
                event.summary,
                actor=event.actor_label or _name(event.actor),
            )
        )

    for version in PostVersion.objects.filter(post=post).select_related("created_by").order_by("version_number"):
        source = " (frozen at approval)" if (version.snapshot or {}).get("_source") == "approval" else ""
        events.append(
            Event(
                version.created_at,
                "version",
                f"Version {version.version_number}{source}",
                actor=_name(version.created_by),
                extra={"version": version.version_number},
            )
        )

    tones = {
        "approved": "success",
        "changes_requested": "warning",
        "rejected": "danger",
        "reopened": "warning",
        "held": "warning",
    }
    for record in (
        ApprovalRecord.objects.filter(post=post)
        .select_related("actor", "version", "platform_post__social_account")
        .order_by("created_at")
    ):
        scope = f" · {record.platform_post.social_account.get_platform_display()}" if record.platform_post else ""
        numbered = f" · version {record.version.version_number}" if record.version else ""
        title = f"{record.get_stage_display()}: {record.get_decision_display().lower()}{scope}{numbered}"
        events.append(
            Event(
                record.created_at,
                record.decision,
                title,
                detail=record.reason,
                actor=_name(record.actor),
                tone=tones.get(record.decision, "neutral"),
            )
        )

    comments = PostComment.objects.filter(post=post, deleted_at__isnull=True).select_related("author")
    if not include_internal:
        comments = comments.filter(visibility=PostComment.Visibility.EXTERNAL)
    for comment in comments.order_by("created_at"):
        pin = getattr(comment, "pin", None) if hasattr(comment, "pin") else None
        where = f" ({pin.media_asset.filename}, {pin.label()})" if pin else ""
        events.append(
            Event(
                comment.created_at,
                "comment",
                f"Comment{where}",
                detail=comment.body,
                actor=_name(comment.author),
                internal=comment.visibility == PostComment.Visibility.INTERNAL,
            )
        )

    for log in (
        PublishLog.objects.filter(platform_post__post=post)
        .select_related("platform_post__social_account")
        .order_by("created_at")
    ):
        ok = not log.error_message and (log.status_code or 200) < 400
        platform = log.platform_post.social_account.get_platform_display()
        events.append(
            Event(
                log.created_at,
                "publish",
                f"{'Published' if ok else 'Publish failed'} on {platform}",
                detail=log.error_message[:300],
                tone="success" if ok else "danger",
            )
        )

    if not include_internal:
        events = [e for e in events if not e.internal]
    return sorted(events, key=lambda e: e.at)
