"""Fingerprints of reviewable content, approval records, reopening on change, and the scheduling check.

The rules (brief, "Client approvals"):

- Any change to media, caption or platform settings after approval reopens it.
- A post cannot be scheduled until its required approvals are complete, for the
  content as it is now. Silence never counts as approval.
- Only the client approves the client stage; the studio owner can override with
  a logged reason.

They apply to workspaces whose approval mode requires approval. In mode "none"
BrightBean's own behaviour is unchanged.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading

from django.db import transaction

REQUIRED_MODES = {"required_internal", "required_internal_and_client"}
REOPEN_FROM = ("approved", "pending_client", "scheduled", "failed", "on_hold", "manual_action")
_state = threading.local()


class ApprovalRequiredError(ValueError):
    """Raised when something tries to schedule content that isn't approved as it is now."""


def requires_approval(workspace) -> bool:
    return getattr(workspace, "approval_workflow_mode", "none") in REQUIRED_MODES


def needs_client(workspace) -> bool:
    return getattr(workspace, "approval_workflow_mode", "none") == "required_internal_and_client"


@contextlib.contextmanager
def bypass():
    """Only for loading fixtures (demo data): skip the reopen and scheduling checks inside the block."""
    previous = getattr(_state, "bypass", False)
    _state.bypass = True
    try:
        yield
    finally:
        _state.bypass = previous


def bypassed() -> bool:
    return getattr(_state, "bypass", False)


# ── Fingerprints ─────────────────────────────────────────────────────────────


# Keys the publisher writes about the outcome (apps.publishing), not settings anyone approves.
SYSTEM_EXTRA_KEYS = frozenset({"live_url", "outcome_unknown"})


def strip_system_keys(extra) -> dict:
    return {k: v for k, v in (extra or {}).items() if k not in SYSTEM_EXTRA_KEYS}


def _authored_extra(platform_post) -> dict:
    extra = strip_system_keys(platform_post.platform_extra)
    if platform_post.social_account_id and platform_post.social_account.platform == "youtube":
        from apps.social_accounts.youtube_compliance import authored_youtube_settings

        return authored_youtube_settings(extra)
    return extra


def content_for(platform_post, *, post=None, media=None) -> dict:
    """Everything a reviewer approves for one platform post: text, media, overrides and settings."""
    post = post or platform_post.post
    platform = platform_post.social_account.platform if platform_post.social_account_id else ""
    if media is None:
        media = list(post.media_attachments.select_related("media_asset").order_by("position"))
    return {
        "title": post.title,
        "caption": post.caption,
        "first_comment": post.first_comment,
        "tags": list(post.tags or []),
        "media": [
            {
                "asset": str(m.media_asset_id),
                "file": m.media_asset.file.name if m.media_asset_id else "",
                "version": str(m.media_asset.current_version_id or ""),
                "position": m.position,
                "alt": m.alt_text,
                "crop": (m.platform_overrides or {}).get(platform, {}),
            }
            for m in media
        ],
        "override_title": platform_post.platform_specific_title,
        "override_caption": platform_post.platform_specific_caption,
        "override_first_comment": platform_post.platform_specific_first_comment,
        "override_media": platform_post.platform_specific_media,
        "settings": _authored_extra(platform_post),
    }


def fingerprint(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def fingerprints_for(post) -> dict[str, str]:
    media = list(post.media_attachments.select_related("media_asset").order_by("position"))
    return {
        str(pp.id): fingerprint(content_for(pp, post=post, media=media))
        for pp in post.platform_posts.select_related("social_account")
    }


# ── Versions and records ─────────────────────────────────────────────────────


def ensure_version(post, user):
    """The PostVersion matching the current content, creating one if the latest doesn't match."""
    from apps.composer.models import PostVersion

    current = fingerprints_for(post)
    latest = PostVersion.objects.filter(post=post).order_by("-version_number").first()
    if latest is not None and (latest.snapshot or {}).get("_fingerprints") == current:
        return latest
    snapshot = {
        "title": post.title,
        "caption": post.caption,
        "first_comment": post.first_comment,
        "tags": post.tags,
        "media": [str(m.media_asset_id) for m in post.media_attachments.order_by("position")],
        "platform_posts": [
            {
                "social_account_id": str(pp.social_account_id),
                "platform": pp.social_account.platform if pp.social_account_id else "",
                "title_override": pp.platform_specific_title,
                "caption_override": pp.platform_specific_caption,
                "first_comment_override": pp.platform_specific_first_comment,
                "platform_extra": _authored_extra(pp),
            }
            for pp in post.platform_posts.select_related("social_account")
        ],
        "_fingerprints": current,
        "_source": "approval",
    }
    number = (
        PostVersion.objects.filter(post=post)
        .order_by("-version_number")
        .values_list("version_number", flat=True)
        .first()
        or 0
    ) + 1
    return PostVersion.objects.create(post=post, version_number=number, snapshot=snapshot, created_by=user)


DECISIONS = {
    "submitted": "submitted",
    "approved": "approved",
    "changes_requested": "changes_requested",
    "rejected": "rejected",
    "resubmitted": "resubmitted",
    "held": "held",
}


def actor_stage(workspace, user) -> str:
    from apps.members.models import WorkspaceMembership
    from apps.reviews.models import ApprovalRecord

    if user is None:
        return ApprovalRecord.Stage.SYSTEM
    role = (
        WorkspaceMembership.objects.filter(user=user, workspace=workspace)
        .values_list("workspace_role", flat=True)
        .first()
    )
    return (
        ApprovalRecord.Stage.CLIENT
        if role == WorkspaceMembership.WorkspaceRole.CLIENT
        else ApprovalRecord.Stage.INTERNAL
    )


def record_action(action) -> None:
    """Called for every new ApprovalAction: store the exact version it was about."""
    from apps.reviews.models import ApprovalRecord

    post = action.post
    workspace = post.workspace
    stage = actor_stage(workspace, action.user)
    reason = ""
    decision = DECISIONS.get(action.action, action.action)
    if action.comment.startswith("Override:"):
        stage, reason = ApprovalRecord.Stage.OVERRIDE, action.comment.removeprefix("Override:").strip()
    elif action.action == "approved" and action.comment == "Hold lifted.":
        decision = ApprovalRecord.Decision.RESUMED  # lifting a hold keeps the earlier approvals
    ApprovalRecord.objects.create(
        workspace=workspace,
        post=post,
        platform_post=action.platform_post,
        action=action,
        version=ensure_version(post, action.user),
        stage=stage,
        decision=decision,
        fingerprints=fingerprints_for(post),
        actor=action.user,
        reason=reason or action.comment,
    )


# ── Is it cleared? ───────────────────────────────────────────────────────────


def clearance(platform_post) -> dict:
    """Which required approvals the platform post has for its content as it is now."""
    from apps.reviews.models import ApprovalRecord

    post = platform_post.post
    workspace = post.workspace
    current = fingerprints_for(post).get(str(platform_post.id))
    records = [r for r in ApprovalRecord.objects.filter(post=post).order_by("created_at") if r.covers(platform_post)]
    internal = client = None
    for record in records:
        matches = record.fingerprints.get(str(platform_post.id)) == current
        if record.decision == ApprovalRecord.Decision.APPROVED and matches:
            if record.stage == ApprovalRecord.Stage.INTERNAL:
                internal = record
            elif record.stage in (ApprovalRecord.Stage.CLIENT, ApprovalRecord.Stage.OVERRIDE):
                client = record
        elif record.decision in (
            ApprovalRecord.Decision.CHANGES_REQUESTED,
            ApprovalRecord.Decision.REJECTED,
            ApprovalRecord.Decision.REOPENED,
        ):
            internal = client = None
    # The client approves what the studio approved: an internal approval must come first.
    if client is not None and internal is not None and client.created_at < internal.created_at:
        client = None
    if client is not None and internal is None:
        # The client approved before any internal approval of this content (possible only via the portal after a reopen).
        client = None
    required_client = needs_client(workspace)
    return {
        "required": requires_approval(workspace),
        "internal": internal,
        "client": client,
        "needs_client": required_client,
        "cleared": (not requires_approval(workspace))
        or (internal is not None and (client is not None or not required_client)),
    }


def check_schedulable(platform_post) -> None:
    """Raise ApprovalRequiredError unless the content as it is now has every required approval."""
    if bypassed():
        return
    state = clearance(platform_post)
    if state["cleared"]:
        return
    if state["internal"] is None:
        raise ApprovalRequiredError(
            "This post needs studio approval of its current version before it can be scheduled."
        )
    raise ApprovalRequiredError(
        "This post needs the client's approval of its current version before it can be scheduled."
    )


def schedule_block_reason(platform_post) -> str | None:
    """Why this platform post can't be committed to the publisher now, or None. For views to show."""
    try:
        check_schedulable(platform_post)
    except ApprovalRequiredError as exc:
        return str(exc)
    return None


# ── Reopening ────────────────────────────────────────────────────────────────


def reopen(post, reason: str, *, platform_post=None) -> list:
    """Send approved/client/scheduled/failed/held children back to studio review after a change."""
    from apps.composer.models import PlatformPost
    from apps.reviews.models import ApprovalRecord

    if bypassed() or not requires_approval(post.workspace):
        return []
    children = PlatformPost.objects.filter(post=post, status__in=REOPEN_FROM)
    if platform_post is not None:
        children = children.filter(pk=platform_post.pk)
    ids = list(children.values_list("id", flat=True))
    if not ids:
        return []
    with transaction.atomic():
        PlatformPost.objects.filter(id__in=ids).update(status="pending_review", next_retry_at=None)
        ApprovalRecord.objects.create(
            workspace=post.workspace,
            post=post,
            platform_post=platform_post,
            stage=ApprovalRecord.Stage.SYSTEM,
            decision=ApprovalRecord.Decision.REOPENED,
            fingerprints=fingerprints_for(post),
            reason=reason,
        )
        from apps.audit.services import record

        record(
            "approval.reopened",
            f"Approval reopened: {reason}.",
            workspace=post.workspace,
            target=post,
            data={"platform_posts": [str(i) for i in ids], "reason": reason},
        )
    return ids


# ── Client stage ─────────────────────────────────────────────────────────────


def check_client_stage(platform_post, user) -> None:
    """Who may approve at each stage. Raises ValueError for anyone else.

    Client stage: only a client user (and, if the client's rule names approvers, one of them).
    Studio stage: anyone with approval rights, unless the rule names studio approvers.
    """
    from apps.reviews.models import ApprovalRecord, ApprovalRule

    workspace = platform_post.post.workspace
    rule = ApprovalRule.objects.filter(workspace=workspace).first()
    if platform_post.status != "pending_client" and actor_stage(workspace, user) == ApprovalRecord.Stage.CLIENT:
        raise ValueError("This version is still with the studio; it reaches you for approval once they've reviewed it.")
    if platform_post.status == "pending_client" and needs_client(workspace):
        if actor_stage(workspace, user) != ApprovalRecord.Stage.CLIENT:
            raise ValueError(
                "This post is waiting for the client's approval. Only the client can approve it now; "
                "the studio owner can override with a stated reason."
            )
        if rule and rule.client_approvers.exists() and not rule.client_approvers.filter(pk=user.pk).exists():
            raise ValueError("Your company's approval rule names other people to approve posts.")
    elif platform_post.status == "pending_review" and rule and rule.internal_approvers.exists():
        if not rule.internal_approvers.filter(pk=user.pk).exists():
            raise ValueError("This client's approval rule names other people to review posts for the studio.")


def due_at(platform_post):
    """When the current approval stage is due, from the client's rule (None outside a review stage)."""
    from datetime import timedelta

    from apps.reviews.models import ApprovalRecord, ApprovalRule

    stage = {"pending_review": "internal", "pending_client": "client"}.get(platform_post.status)
    if stage is None:
        return None
    rule = ApprovalRule.objects.filter(workspace=platform_post.post.workspace).first()
    hours = (rule.client_hours if rule else 48) if stage == "client" else (rule.internal_hours if rule else 24)
    started = (
        ApprovalRecord.objects.filter(
            post=platform_post.post, decision__in=["submitted", "resubmitted", "approved", "reopened"]
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    return started + timedelta(hours=hours) if started else None


def attach_pin(comment, data) -> None:
    """Pin a comment to a spot on an image or a moment in a video, from posted fields (pin_asset, pin_x, pin_y, pin_t)."""
    from apps.reviews.models import CommentPin

    asset_id = data.get("pin_asset")
    if not asset_id:
        return
    asset = comment.post.media_attachments.filter(media_asset_id=asset_id).select_related("media_asset").first()
    if asset is None:
        return  # only media on this post

    def num(name, lo, hi):
        try:
            value = float(data.get(name, ""))
        except (TypeError, ValueError):
            return None
        return value if lo <= value <= hi else None

    x, y, t = num("pin_x", 0, 1), num("pin_y", 0, 1), num("pin_t", 0, 86400)
    if t is None and (x is None or y is None):
        return
    CommentPin.objects.create(
        comment=comment,
        media_asset=asset.media_asset,
        x=x if t is None else None,
        y=y if t is None else None,
        timestamp_seconds=t,
    )


def override_client_approval(post, user, reason: str) -> list:
    """The studio owner approves on the client's behalf, with a reason in the record and the audit log."""
    from apps.approvals.models import ApprovalAction
    from apps.audit.services import record
    from apps.members.models import OrgMembership
    from apps.studio.permissions import has_studio_permission

    reason = reason.strip()
    if not reason:
        raise ValueError("Give a reason for overriding the client's approval.")
    membership = OrgMembership.objects.filter(user=user, organization=post.workspace.organization).first()
    if not has_studio_permission(membership, "override_approval"):
        raise PermissionError("Only people allowed to override approvals can do this.")
    moved = []
    with transaction.atomic():
        for pp in post.platform_posts.filter(status="pending_client"):
            pp.transition_to("approved")
            pp.save(update_fields=[*type(pp).TRANSITION_FIELDS, "updated_at"])
            moved.append(pp)
        if moved:
            ApprovalAction.objects.create(
                post=post,
                platform_post=None,
                user=user,
                action=ApprovalAction.ActionType.APPROVED,
                comment=f"Override: {reason}",
            )
            record(
                "approval.override",
                f"Client approval overridden: {reason}",
                workspace=post.workspace,
                actor=user,
                target=post,
                data={"reason": reason},
            )
    return moved
