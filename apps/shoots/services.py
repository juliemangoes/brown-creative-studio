"""Shoots: conflicts, shot lists from planned posts, on-set sync, matching uploads, follow-ups, runway."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import record
from apps.shoots.models import Shoot, Shot

DEFAULT_LENGTH = timedelta(hours=4)


def crew_only(membership) -> bool:
    """Contributors (freelancers) without ``manage_shoots`` see only the shoots they are crew on."""
    from apps.members.models import WorkspaceMembership

    return membership.workspace_role == WorkspaceMembership.WorkspaceRole.CONTRIBUTOR and not (
        membership.effective_permissions.get("manage_shoots", False)
    )


def _span(shoot):
    start = datetime.combine(shoot.date, shoot.call_time)
    end = datetime.combine(shoot.date, shoot.wrap_time) if shoot.wrap_time else start + DEFAULT_LENGTH
    return start, max(end, start + timedelta(minutes=30))


def _norm(values):
    return {str(v).strip().lower(): str(v).strip() for v in values if str(v).strip()}


def visible_workspace_ids(user, organization) -> set:
    """Clients whose shoots this person may see (staff memberships with ``view_shoots``)."""
    from apps.members.models import WorkspaceMembership

    return {
        m.workspace_id
        for m in WorkspaceMembership.objects.filter(user=user, workspace__organization=organization)
        .exclude(workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT)
        .select_related("custom_role")
        if m.effective_permissions.get("view_shoots", False)
    }


def conflicts(shoot, visible=None) -> list[str]:
    """People, equipment and locations double-booked with other shoots in the studio at overlapping times.

    ``visible`` is the set of client workspaces the reader may see; a clashing shoot for any
    other client is described without its title or client (``None`` means no masking, for
    internal checks that only count conflicts).
    """
    if shoot.status == Shoot.Status.CANCELLED:
        return []
    start, end = _span(shoot)
    others = (
        Shoot.objects.filter(workspace__organization_id=shoot.workspace.organization_id, date=shoot.date)
        .exclude(pk=shoot.pk)
        .exclude(status=Shoot.Status.CANCELLED)
        .select_related("workspace")
        .prefetch_related("crew")
    )
    crew = {u.pk: u for u in shoot.crew.all()} if shoot.pk else {}
    gear, places = _norm(shoot.equipment), _norm(shoot.locations)
    found = []
    for other in others:
        o_start, o_end = _span(other)
        if not (start < o_end and o_start < end):
            continue
        if visible is None or other.workspace_id in visible:
            where = f"{other.title} ({other.workspace.name}, {o_start:%H:%M}–{o_end:%H:%M})"
        else:
            where = f"another client's shoot ({o_start:%H:%M}–{o_end:%H:%M})"
        for user in other.crew.all():
            if user.pk in crew:
                found.append(f"{user.name or user.email} is also on {where}.")
        for key in gear.keys() & _norm(other.equipment).keys():
            found.append(f"{gear[key]} is also booked for {where}.")
        for key in places.keys() & _norm(other.locations).keys():
            found.append(f"{places[key]} is also used by {where}.")
    return found


def next_code(shoot) -> str:
    numbers = [
        int(m.group(1)) for c in shoot.shots.values_list("code", flat=True) if (m := re.match(r"S(\d+)", c or ""))
    ]
    return f"S{(max(numbers) + 1) if numbers else 1:02d}"


@transaction.atomic
def add_shot(
    shoot,
    *,
    description,
    post=None,
    format="",
    location="",
    priority=Shot.Priority.MUST,
    unplanned=False,
    shot_id=None,
    user=None,
):
    shot = Shot(
        shoot=shoot,
        workspace=shoot.workspace,
        post=post,
        code=next_code(shoot),
        description=description[:300],
        format=format,
        location=location[:160],
        priority=priority,
        is_unplanned=unplanned,
        position=shoot.shots.count(),
    )
    if shot_id:
        shot.id = shot_id
    shot.save()
    if post is not None:
        from apps.planning.models import ContentPlan
        from apps.planning.services import plan_for, set_stage

        plan = plan_for(post)
        if plan.stage in (ContentPlan.Stage.IDEA, ContentPlan.Stage.PLANNED):
            set_stage(plan, ContentPlan.Stage.SHOT_NEEDED, user, note=f"added to {shoot.title}")
        record(
            "shoot.shot_added",
            f"Shot {shot.code} added to {shoot.title} on {shoot.date:%d %b}",
            workspace=shoot.workspace,
            actor=user,
            target=post,
        )
    return shot


def candidate_posts(shoot, days=45):
    """Planned posts that still need a shot, from the shoot date onwards."""
    from apps.composer.models import Post

    already = (
        Shot.objects.filter(post__isnull=False, shoot__workspace=shoot.workspace)
        .exclude(status=Shot.Status.SKIPPED)
        .values_list("post_id", flat=True)
    )
    tz = timezone.get_current_timezone()
    lo = timezone.make_aware(datetime.combine(shoot.date - timedelta(days=7), datetime.min.time()), tz)
    hi = lo + timedelta(days=days)
    return (
        Post.objects.filter(workspace=shoot.workspace)
        .exclude(id__in=already)
        .filter(
            Q(content_plan__stage__in=["idea", "planned", "shot_needed"])
            | Q(content_plan__isnull=True, media_attachments__isnull=True)
        )
        .filter(
            Q(scheduled_at__gte=lo, scheduled_at__lt=hi)
            | Q(scheduled_at__isnull=True, proposed_publish_at__gte=lo, proposed_publish_at__lt=hi)
            | Q(content_plan__target_date__gte=lo.date(), content_plan__target_date__lt=hi.date())
        )
        .exclude(platform_posts__status__in=["published", "publishing"])
        .select_related("content_plan")
        .distinct()
        .order_by("proposed_publish_at", "scheduled_at")
    )


def shots_from_posts(shoot, posts, user=None) -> list[Shot]:
    made = []
    for post in posts:
        try:
            plan = post.content_plan
        except Exception:  # noqa: BLE001
            plan = None
        description = (plan.hook if plan and plan.hook else "") or post.title or post.caption[:120] or "Shot"
        made.append(add_shot(shoot, description=description, post=post, format=plan.format if plan else "", user=user))
    return made


def mark_shot(shot, status, user=None, *, at=None) -> None:
    if status not in Shot.Status.values or shot.status == status:
        return
    shot.status = status
    shot.captured_at = (at or timezone.now()) if status == Shot.Status.CAPTURED else shot.captured_at
    shot.captured_by = user if status == Shot.Status.CAPTURED else shot.captured_by
    shot.save(update_fields=["status", "captured_at", "captured_by", "updated_at"])
    if shot.post_id and status in (Shot.Status.CAPTURED, Shot.Status.RETAKE):
        from apps.planning.models import ContentPlan
        from apps.planning.services import plan_for, set_stage

        if status == Shot.Status.CAPTURED:
            plan = plan_for(shot.post)
            if plan.stage in (ContentPlan.Stage.IDEA, ContentPlan.Stage.PLANNED, ContentPlan.Stage.SHOT_NEEDED):
                set_stage(plan, ContentPlan.Stage.IN_PRODUCTION, user, note=f"shot {shot.code} captured")
        record(
            f"shoot.shot_{status}",
            f"Shot {shot.code} {'captured' if status == 'captured' else 'flagged for a retake'} on set ({shot.shoot.title})",
            workspace=shot.workspace,
            actor=user,
            target=shot.post,
        )


def apply_ops(shoot, user, ops) -> dict:
    """Apply changes queued by on-set mode. Each op is idempotent, so replays after a reconnect are harmless."""
    applied, skipped = 0, 0
    shots = {str(s.id): s for s in shoot.shots.select_related("post")}
    for op in ops[:500]:
        kind = op.get("op")
        shot = shots.get(str(op.get("shot", "")))
        at = None
        if op.get("at"):
            try:
                at = datetime.fromisoformat(str(op["at"]).replace("Z", "+00:00"))
            except ValueError:
                at = None
        if kind == "status" and shot:
            mark_shot(shot, str(op.get("status", "")), user, at=at)
            applied += 1
        elif kind == "note" and shot:
            shot.notes = str(op.get("text", ""))[:4000]
            shot.save(update_fields=["notes", "updated_at"])
            applied += 1
        elif kind == "idea" and op.get("id") and op.get("text"):
            import uuid

            try:
                new_id = uuid.UUID(str(op["id"]))
            except ValueError:
                skipped += 1
                continue
            if new_id.hex not in {uuid.UUID(k).hex for k in shots}:
                created = add_shot(shoot, description=str(op["text"]), unplanned=True, shot_id=new_id, user=user)
                shots[str(created.id)] = created
                if op.get("status"):
                    mark_shot(created, str(op["status"]), user, at=at)
            applied += 1
        else:
            skipped += 1
    return {"applied": applied, "skipped": skipped}


CODE_RE = re.compile(r"(?<![A-Za-z0-9])S(\d{1,3})(?![0-9])", re.IGNORECASE)


def match_code(filename: str, shots) -> Shot | None:
    """A shot whose code appears in the file name (S03, s03_tacos.jpg, Casa-S3.mov)."""
    match = CODE_RE.search(filename)
    if not match:
        return None
    number = int(match.group(1))
    return next((s for s in shots if (m := re.match(r"S(\d+)", s.code or "")) and int(m.group(1)) == number), None)


def attach_to_shot(shot, asset, user=None) -> None:
    """Captured material for a shot: kept on the shot and attached to its post if the post has no media yet."""
    from apps.composer.models import PostMedia

    shot.media.add(asset)
    if shot.status in (Shot.Status.PLANNED, Shot.Status.RETAKE):
        mark_shot(shot, Shot.Status.CAPTURED, user)
    if shot.post_id and not PostMedia.objects.filter(post_id=shot.post_id).exists():
        PostMedia.objects.create(post_id=shot.post_id, media_asset=asset, position=0)
        record(
            "shoot.media_matched",
            f"{asset.filename} from {shot.shoot.title} attached (shot {shot.code})",
            workspace=shot.workspace,
            actor=user,
            target=shot.post,
        )


def upload_and_match(shoot, user, uploads) -> tuple[list, list, list[str]]:
    """Store uploads in the client's media library (scanned first) and match them to shots by code."""
    from django.core.exceptions import ValidationError

    from apps.media_library.services import create_asset
    from apps.studio import scanning
    from apps.studio.files import check_upload

    shots = list(shoot.shots.all())
    matched, unmatched, refused = [], [], []
    for upload in uploads:
        try:
            if upload.name.lower().endswith((".mp4", ".mov", ".webm")):
                result = scanning.scan(upload)  # videos: the media library checks the format, we check for malware
                if not result.accepted:
                    raise ValidationError(
                        f"{upload.name} was refused: {result.detail or 'it could not be checked for malware'}."
                    )
            else:
                check_upload(upload, ["image"])
            asset = create_asset(
                shoot.workspace.organization,
                shoot.workspace,
                upload,
                user,
                title=upload.name,
                tags=["shoot", shoot.date.isoformat()],
                source="shoot",
            )
        except ValidationError as exc:
            refused.extend(exc.messages)
            continue
        from apps.assets.models import AssetRights

        AssetRights.objects.get_or_create(
            media_asset=asset, defaults={"source": AssetRights.Source.STUDIO, "shoot": shoot, "updated_by": user}
        )
        shot = match_code(upload.name, shots)
        if shot:
            attach_to_shot(shot, asset, user)
            matched.append((asset, shot))
        else:
            unmatched.append(asset)
    return matched, unmatched, refused


def create_followups(shoot, user) -> list:
    """Missing must-have shots become follow-up tasks."""
    from apps.projects.models import Task
    from apps.projects.services import create_from_template, ensure_builtin_templates

    missing = list(
        shoot.shots.filter(
            status__in=[Shot.Status.PLANNED, Shot.Status.RETAKE], priority=Shot.Priority.MUST
        ).select_related("post")
    )
    if not missing:
        return []
    project = shoot.project
    if project is None:
        template = ensure_builtin_templates(shoot.workspace.organization)["shoot"]
        project = create_from_template(
            shoot.workspace,
            template,
            name=f"{shoot.title} follow-ups",
            starts_on=timezone.localdate(),
            owner=user,
            created_by=user,
        )
        project.tasks.all().delete()
        shoot.project = project
        shoot.save(update_fields=["project", "updated_at"])
    tasks = []
    for shot in missing:
        title = f"{'Retake' if shot.status == Shot.Status.RETAKE else 'Missing shot'} {shot.code}: {shot.description}"[
            :200
        ]
        if project.tasks.filter(title=title).exists():
            continue
        tasks.append(
            Task.objects.create(
                workspace=shoot.workspace,
                project=project,
                title=title,
                due_on=timezone.localdate() + timedelta(days=7),
                assignee=user,
                created_by=user,
                description=(f"For “{shot.post.title}”. " if shot.post_id else "") + (shot.notes or ""),
            )
        )
    record(
        "shoot.followups",
        f"{len(tasks)} follow-up task{'s' if len(tasks) != 1 else ''} for missing shots from {shoot.title}",
        workspace=shoot.workspace,
        actor=user,
        target=shoot,
    )
    return tasks


def runway(workspace, today=None, days=60) -> dict:
    """How many planned posts are covered by captured material, and when the next shoot is needed."""
    from apps.composer.models import Post

    today = today or timezone.localdate()
    tz = timezone.get_current_timezone()
    lo = timezone.make_aware(datetime.combine(today, datetime.min.time()), tz)
    hi = lo + timedelta(days=days)
    posts = (
        Post.objects.filter(workspace=workspace)
        .filter(
            Q(scheduled_at__gte=lo, scheduled_at__lt=hi)
            | Q(scheduled_at__isnull=True, proposed_publish_at__gte=lo, proposed_publish_at__lt=hi)
        )
        .exclude(platform_posts__status__in=["published", "publishing", "rejected"])
        .select_related("content_plan")
        .prefetch_related("media_attachments", "shots")
        .distinct()
    )
    covered: list[tuple] = []
    uncovered: list[tuple] = []
    for post in posts:
        plan = getattr(post, "content_plan", None)
        fmt = plan.format if plan else ""
        has_material = bool(post.media_attachments.all()) or any(
            s.status == Shot.Status.CAPTURED and s.media.exists()
            for s in getattr(post, "shots").all()  # noqa: B009 - reverse relation from this app
        )
        day = timezone.localtime(post.scheduled_at or post.proposed_publish_at).date()
        (covered if has_material or fmt == "text" else uncovered).append((day, post))
    uncovered.sort(key=lambda x: x[0])
    upcoming = (
        Shoot.objects.filter(workspace=workspace, date__gte=today)
        .exclude(status=Shoot.Status.CANCELLED)
        .order_by("date")
        .first()
    )
    first_gap = uncovered[0][0] if uncovered else None
    needed_by = (first_gap - timedelta(days=5)) if first_gap else None
    return {
        "covered": len(covered),
        "uncovered": len(uncovered),
        "total": len(covered) + len(uncovered),
        "first_gap": first_gap,
        "needed_by": needed_by,
        "next_shoot": upcoming,
        "late": bool(needed_by and (upcoming is None or upcoming.date > needed_by)),
        "uncovered_posts": [p for _, p in uncovered[:10]],
    }


def readiness(shoot) -> list[str]:
    """What is still missing before a shoot day, in plain words (empty when it is ready)."""
    missing = []
    shots = list(shoot.shots.all())
    if not shots:
        missing.append("No shot list yet")
    else:
        unlinked = sum(1 for s in shots if s.priority == Shot.Priority.MUST and not s.post_id and not s.is_unplanned)
        if unlinked:
            missing.append(f"{unlinked} must-have shot{'s' if unlinked != 1 else ''} not tied to a post")
    if not shoot.crew.exists():
        missing.append("No crew")
    if not [loc for loc in shoot.locations if str(loc).strip()]:
        missing.append("No location")
    needs_release = [t for t in shoot.talent if isinstance(t, dict) and t.get("name") and not t.get("release")]
    if needs_release:
        missing.append(f"{len(needs_release)} release form{'s' if len(needs_release) != 1 else ''} outstanding")
    if not shoot.prep_sent_at:
        missing.append("Client prep not sent")
    if conflicts(shoot):
        missing.append("Booking conflict")
    return missing


def posts_needing_shots(workspace_ids, today=None, days=21):
    """Planned posts marked "shot needed" in the next ``days`` that are on no upcoming shoot's list."""
    from apps.planning.models import ContentPlan

    today = today or timezone.localdate()
    on_a_shoot = Shot.objects.filter(
        workspace_id__in=workspace_ids,
        post__isnull=False,
        shoot__date__gte=today,
        shoot__status__in=[Shoot.Status.PLANNED, Shoot.Status.CONFIRMED],
    ).values("post_id")
    planned = (
        ContentPlan.objects.filter(
            workspace_id__in=workspace_ids,
            stage=ContentPlan.Stage.SHOT_NEEDED,
            target_date__gte=today,
            target_date__lte=today + timedelta(days=days),
        )
        .exclude(post_id__in=on_a_shoot)
        .select_related("post", "workspace")
        .order_by("target_date")
    )
    return list(planned[:20])
