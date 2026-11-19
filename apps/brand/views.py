"""Studio brand pages for one client: profile, stages and files.

Viewing needs ``view_client_profile``; changing needs ``manage_brand``. Stages and
files are always fetched through the URL's workspace, so another client's id is a 404.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record
from apps.brand.forms import AssetUploadForm, BrandProfileForm, StageForm
from apps.brand.models import BrandAsset, BrandStage
from apps.brand.services import (
    add_asset,
    ensure_stages,
    hex_to_rgb,
    logo_groups,
    profile_for,
    send_for_signoff,
    stages_for,
)
from apps.clients.views import _context as client_context
from apps.members.decorators import require_permission
from apps.projects.defaults import ALL_BRAND_STAGES, STRATEGY_STAGES
from apps.studio.decorators import in_local_time
from apps.studio.permissions import workspace_permission


def _ctx(request, tab):
    ctx = client_context(request, "brand")
    ctx.update({"brand_tab": tab, "can_manage_brand": workspace_permission(request, "manage_brand")})
    return ctx


def _require_manage(request):
    if not workspace_permission(request, "manage_brand"):
        raise PermissionDenied("Permission denied: manage_brand")


@require_permission("view_client_profile")
@in_local_time
def brand_profile(request, workspace_id):
    ctx = _ctx(request, "profile")
    profile = profile_for(request.workspace)
    colors = [{**c, "rgb": hex_to_rgb(c["hex"])} for c in profile.colors]
    ctx.update(
        {
            "brand": profile,
            "colors": colors,
            "form": BrandProfileForm(profile=profile) if ctx["can_manage_brand"] else None,
        }
    )
    return render(request, "brand/profile.html", ctx)


@require_permission("view_client_profile")
@require_POST
def brand_profile_save(request, workspace_id):
    _require_manage(request)
    profile = profile_for(request.workspace)
    form = BrandProfileForm(request.POST, profile=profile)
    if form.is_valid():
        form.apply(profile)
        profile.updated_by = request.user
        profile.save()
        messages.success(
            request,
            "Brand profile saved." + (" The client's Brand Hub shows the changes now." if profile.is_published else ""),
        )
        return redirect("brand:profile", workspace_id=workspace_id)
    ctx = _ctx(request, "profile")
    ctx.update({"brand": profile, "colors": [], "form": form, "editing": True})
    return render(request, "brand/profile.html", ctx, status=400)


@require_permission("view_client_profile")
@require_POST
def brand_publish(request, workspace_id):
    _require_manage(request)
    profile = profile_for(request.workspace)
    profile.is_published = request.POST.get("publish") == "1"
    profile.published_at = timezone.now() if profile.is_published else profile.published_at
    profile.save(update_fields=["is_published", "published_at", "updated_at"])
    record(
        "brand.published" if profile.is_published else "brand.unpublished",
        f"The Brand Hub was {'shared with' if profile.is_published else 'hidden from'} {request.workspace.name}.",
        workspace=request.workspace,
        actor=request.user,
        target=profile,
    )
    messages.success(
        request,
        "The Brand Hub is now visible to the client."
        if profile.is_published
        else "The Brand Hub is hidden from the client.",
    )
    return redirect("brand:profile", workspace_id=workspace_id)


# ── Stages ───────────────────────────────────────────────────────────────────


@require_permission("view_client_profile")
@in_local_time
def brand_stages(request, workspace_id):
    ctx = _ctx(request, "stages")
    stages = list(stages_for(request.workspace).select_related("project", "milestone", "signed_off_by"))
    ctx.update(
        {
            "stages": [
                (s, StageForm(instance=s, prefix=str(s.pk)) if ctx["can_manage_brand"] else None) for s in stages
            ],
            "has_stages": bool(stages),
        }
    )
    return render(request, "brand/stages.html", ctx)


@require_permission("view_client_profile")
@require_POST
def brand_stages_start(request, workspace_id):
    _require_manage(request)
    keys = STRATEGY_STAGES if request.POST.get("set") == "strategy" else ALL_BRAND_STAGES
    ensure_stages(request.workspace, keys)
    messages.success(request, "Brand stages set up. Start a brand project in Projects to plan the tasks behind them.")
    return redirect("brand:stages", workspace_id=workspace_id)


def _stage(request, stage_id):
    return get_object_or_404(BrandStage.objects.filter(workspace=request.workspace), pk=stage_id)


@require_permission("view_client_profile")
@require_POST
def brand_stage_save(request, workspace_id, stage_id):
    _require_manage(request)
    stage = _stage(request, stage_id)
    form = StageForm(request.POST, instance=stage, prefix=str(stage.pk))
    if form.is_valid():
        form.save()
        messages.success(request, f"{stage.get_key_display()} saved.")
    else:
        messages.error(request, "Check the stage details.")
    return redirect("brand:stages", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def brand_stage_send(request, workspace_id, stage_id):
    _require_manage(request)
    stage = _stage(request, stage_id)
    summary = request.POST.get("client_summary", "").strip()
    if not summary:
        messages.error(request, "Write what the client is signing off before sending it.")
        return redirect("brand:stages", workspace_id=workspace_id)
    sent = send_for_signoff(stage, request.user, summary)
    note = (
        f" {sent} client contact{'s' if sent != 1 else ''} emailed."
        if sent
        else " No client user can approve yet: invite one from Client settings."
    )
    messages.success(request, f"{stage.get_key_display()} sent for sign-off.{note}")
    return redirect("brand:stages", workspace_id=workspace_id)


# ── Files ────────────────────────────────────────────────────────────────────


@require_permission("view_client_profile")
@in_local_time
def brand_files(request, workspace_id):
    ctx = _ctx(request, "files")
    assets = list(BrandAsset.objects.filter(workspace=request.workspace).select_related("stage", "uploaded_by"))
    ctx.update(
        {
            "logos": logo_groups([a for a in assets if a.kind == BrandAsset.Kind.LOGO]),
            "other_assets": [a for a in assets if a.kind != BrandAsset.Kind.LOGO],
            "client_uploads": [a for a in assets if a.source == BrandAsset.Source.CLIENT],
            "upload_form": AssetUploadForm(workspace=request.workspace) if ctx["can_manage_brand"] else None,
        }
    )
    return render(request, "brand/files.html", ctx)


@require_permission("view_client_profile")
@require_POST
def brand_files_upload(request, workspace_id):
    _require_manage(request)
    form = AssetUploadForm(request.POST, request.FILES, workspace=request.workspace)
    if not form.is_valid():
        messages.error(
            request, "Choose at least one file. " + " ".join(e for errs in form.errors.values() for e in errs)
        )
        return redirect("brand:files", workspace_id=workspace_id)
    data, added, refused = form.cleaned_data, 0, []
    for upload in data["files"]:
        try:
            add_asset(
                request.workspace,
                upload,
                user=request.user,
                kind=data["kind"],
                title=data["title"],
                variant=data["variant"],
                stage=data["stage"],
                client_visible=data["client_visible"],
            )
            added += 1
        except ValidationError as exc:
            refused.extend(exc.messages)
    if added:
        messages.success(request, f"{added} file{'s' if added != 1 else ''} added.")
    for message in refused:
        messages.error(request, message)
    return redirect("brand:files", workspace_id=workspace_id)


def _asset(request, asset_id):
    return get_object_or_404(BrandAsset.objects.filter(workspace=request.workspace), pk=asset_id)


@require_permission("view_client_profile")
def brand_file_download(request, workspace_id, asset_id):
    """A fresh short-lived signed link, so links on an open page never go stale."""
    asset = _asset(request, asset_id)
    return redirect(asset.signed_url(inline=request.GET.get("inline") == "1"))


@require_permission("view_client_profile")
@require_POST
def brand_file_visibility(request, workspace_id, asset_id):
    _require_manage(request)
    asset = _asset(request, asset_id)
    asset.client_visible = request.POST.get("visible") == "1"
    asset.save(update_fields=["client_visible"])
    return redirect("brand:files", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def brand_file_delete(request, workspace_id, asset_id):
    _require_manage(request)
    asset = _asset(request, asset_id)
    name = asset.original_name
    asset.file.delete(save=False)
    asset.delete()
    record(
        "brand.file_removed",
        f"{name} was removed from the brand files.",
        workspace=request.workspace,
        actor=request.user,
    )
    messages.success(request, f"{name} removed.")
    return redirect("brand:files", workspace_id=workspace_id)
