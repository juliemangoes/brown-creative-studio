"""Brand profile, stage sign-off and brand files."""

from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext

from apps.audit.services import record
from apps.brand.models import BrandAsset, BrandProfile, BrandStage
from apps.projects.defaults import ALL_BRAND_STAGES, BRAND_STAGES, STRATEGY_STAGES
from apps.projects.models import ProjectKind, Task

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
STAGE_POSITION = {key: i for i, (key, _, _) in enumerate(BRAND_STAGES)}


def profile_for(workspace) -> BrandProfile:
    profile, _ = BrandProfile.objects.get_or_create(workspace=workspace)
    return profile


def stages_for(workspace):
    return BrandStage.objects.filter(workspace=workspace).order_by("position")


@transaction.atomic
def ensure_stages(workspace, keys, project=None) -> list[BrandStage]:
    """Create the brand stages a project needs (once per client) and link them to its sign-off milestones."""
    stages = []
    for key in keys:
        stage, _ = BrandStage.objects.get_or_create(
            workspace=workspace, key=key, defaults={"position": STAGE_POSITION[key]}
        )
        if project is not None:
            stage.project = project
            stage.milestone = Task.objects.filter(project=project, template_key=f"{key}_signoff").first()
            if key == "identity" and stage.revision_limit is None:
                stage.revision_limit = 2  # the contract sets the real number (Phase 5)
            stage.save()
        stages.append(stage)
    first = next((s for s in stages if s.status == BrandStage.Status.NOT_STARTED), None)
    if first and not any(s.status != BrandStage.Status.NOT_STARTED for s in stages):
        first.status = BrandStage.Status.IN_PROGRESS
        first.save(update_fields=["status", "updated_at"])
    return stages


def link_brand_project(project) -> list[BrandStage]:
    """After a brand or strategy project is created: its stages, tied to its sign-off milestones."""
    if project.kind == ProjectKind.BRAND:
        return ensure_stages(project.workspace, ALL_BRAND_STAGES, project)
    if project.kind == ProjectKind.STRATEGY:
        return ensure_stages(project.workspace, STRATEGY_STAGES, project)
    return []


def send_for_signoff(stage: BrandStage, actor, summary: str) -> int:
    """Ask the client to sign off a stage. Returns how many client users were emailed."""
    from apps.studio.portal import client_users, send_portal_email

    stage.status = BrandStage.Status.AWAITING_SIGNOFF
    stage.client_summary = summary
    stage.sent_at = timezone.now()
    stage.client_comment = ""
    stage.save(update_fields=["status", "client_summary", "sent_at", "client_comment", "updated_at"])
    record(
        "brand.sent_for_signoff",
        f"{stage.get_key_display()} was sent to {stage.workspace.name} for sign-off.",
        workspace=stage.workspace,
        actor=actor,
        target=stage,
    )
    sent = 0
    for user in client_users(stage.workspace, "portal_approve"):
        sent += send_portal_email(
            user,
            stage.workspace,
            subject=lambda: (
                gettext("%(client)s: %(stage)s is ready for your sign-off")
                % {"client": stage.workspace.name, "stage": stage.get_key_display()}
            ),
            template="brand/email/signoff_request",
            context={"stage": stage},
        )
    return sent


@transaction.atomic
def sign_off(stage: BrandStage, user, comment: str = "") -> None:
    stage.status = BrandStage.Status.SIGNED_OFF
    stage.signed_off_at = timezone.now()
    stage.signed_off_by = user
    stage.client_comment = comment
    stage.save(update_fields=["status", "signed_off_at", "signed_off_by", "client_comment", "updated_at"])
    if stage.milestone_id:
        Task.objects.filter(pk=stage.milestone_id).update(status=Task.Status.DONE, completed_at=timezone.now())
    following = (
        stages_for(stage.workspace).filter(position__gt=stage.position, status=BrandStage.Status.NOT_STARTED).first()
    )
    if following:
        following.status = BrandStage.Status.IN_PROGRESS
        following.save(update_fields=["status", "updated_at"])
    record(
        "brand.stage_signed_off",
        f"{user.name or user.email} signed off {stage.get_key_display()}.",
        workspace=stage.workspace,
        actor=user,
        target=stage,
        data={"comment": comment} if comment else None,
    )


def request_changes(stage: BrandStage, user, comment: str) -> None:
    stage.status = BrandStage.Status.CHANGES_REQUESTED
    stage.client_comment = comment
    if stage.key == "identity":
        stage.revisions_used += 1
    stage.save(update_fields=["status", "client_comment", "revisions_used", "updated_at"])
    record(
        "brand.changes_requested",
        f"{user.name or user.email} asked for changes to {stage.get_key_display()}.",
        workspace=stage.workspace,
        actor=user,
        target=stage,
        data={"comment": comment, "revisions_used": stage.revisions_used},
    )


def add_asset(
    workspace,
    uploaded_file,
    *,
    user,
    kind,
    title="",
    variant="",
    stage=None,
    client_visible=False,
    source=BrandAsset.Source.STUDIO,
    notes="",
) -> BrandAsset:
    """Validate, scan and store one brand file. Raises ValidationError when refused."""
    asset = BrandAsset(
        workspace=workspace,
        kind=kind,
        title=(title or uploaded_file.name.rsplit(".", 1)[0])[:160],
        variant=variant[:80],
        stage=stage,
        client_visible=client_visible,
        source=source,
        notes=notes,
    )
    asset.attach(uploaded_file, BrandAsset.ALLOWED_KINDS, user=user)
    asset.save()
    record(
        "brand.file_added",
        f"{asset.original_name} was added to {workspace.name}'s brand files.",
        workspace=workspace,
        actor=user,
        target=asset,
        data={"scan": asset.scan_status, "source": source},
    )
    return asset


# ── Parsing simple list fields ───────────────────────────────────────────────


def lines(text: str) -> list[str]:
    return [line.strip(" -•\t") for line in (text or "").splitlines() if line.strip(" -•\t")]


def pairs(text: str, second="description") -> list[dict]:
    """'Name: description' per line."""
    out = []
    for line in lines(text):
        name, _, rest = line.partition(":")
        out.append({"name": name.strip(), second: rest.strip()})
    return out


def unlines(values) -> str:
    return "\n".join(values or [])


def unpairs(values, second="description") -> str:
    return "\n".join(f"{v.get('name', '')}: {v.get(second, '')}".rstrip(": ") for v in values or [])


def hex_to_rgb(value: str) -> str:
    value = value.lstrip("#")
    return ", ".join(str(int(value[i : i + 2], 16)) for i in (0, 2, 4))


def logo_groups(assets):
    """Logos grouped by title and variant, each with its formats (for downloads by format)."""
    groups: dict = {}
    for asset in assets:
        groups.setdefault((asset.title, asset.variant), []).append(asset)
    result = []
    for (title, variant), items in groups.items():
        preview = next((a for a in items if a.file_kind == "svg"), None) or next(
            (a for a in items if a.file_kind == "image"), None
        )
        result.append({"title": title, "variant": variant, "files": items, "preview": preview})
    return result
