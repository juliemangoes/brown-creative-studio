"""The Brand Hub in the client portal, and the stage sign-off actions.

Only what the studio shared is shown: a published profile (never its internal
notes), files marked for the client, and stages sent for sign-off.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from apps.brand.models import BrandAsset, BrandProfile, BrandStage
from apps.brand.services import hex_to_rgb, logo_groups, request_changes, sign_off
from apps.studio.portal import (
    PortalNotice,
    PortalSection,
    portal_permission_required,
    portal_permissions,
    register_notice,
    register_section,
)

VISIBLE_STAGE_STATUSES = (
    BrandStage.Status.AWAITING_SIGNOFF,
    BrandStage.Status.CHANGES_REQUESTED,
    BrandStage.Status.SIGNED_OFF,
)


def _hub_has_content(workspace) -> bool:
    return (
        BrandProfile.objects.filter(workspace=workspace, is_published=True).exists()
        or BrandStage.objects.filter(workspace=workspace, status__in=VISIBLE_STAGE_STATUSES).exists()
        or BrandAsset.objects.filter(workspace=workspace, client_visible=True).exists()
    )


@register_section
def _section(request):
    workspace = request.portal_workspace
    if portal_permissions(request).get("portal_view_content") and _hub_has_content(workspace):
        return PortalSection("brand_portal", gettext_lazy("Brand Hub"), reverse("brand_portal:hub"), order=40)
    return None


@register_notice
def _waiting(request):
    if not portal_permissions(request).get("portal_approve"):
        return None
    if getattr(request.resolver_match, "namespace", "") == "brand_portal":
        return None
    stage = (
        BrandStage.objects.filter(workspace=request.portal_workspace, status=BrandStage.Status.AWAITING_SIGNOFF)
        .order_by("position")
        .first()
    )
    if stage is None:
        return None
    return PortalNotice(
        _("%(stage)s is ready for your sign-off.") % {"stage": stage.get_key_display()},
        reverse("brand_portal:hub") + "#signoff",
        _("Review"),
    )


@portal_permission_required("portal_view_content")
@require_GET
def hub(request):
    workspace = request.portal_workspace
    profile = BrandProfile.objects.filter(workspace=workspace, is_published=True).first()
    assets = list(BrandAsset.objects.filter(workspace=workspace, client_visible=True))
    stages = list(BrandStage.objects.filter(workspace=workspace).order_by("position"))
    return render(
        request,
        "brand/portal/hub.html",
        {
            "workspace": workspace,
            "brand": profile,
            "colors": [{**c, "rgb": hex_to_rgb(c["hex"])} for c in profile.colors] if profile else [],
            "logos": logo_groups([a for a in assets if a.kind == BrandAsset.Kind.LOGO]),
            "fonts_files": [a for a in assets if a.kind == BrandAsset.Kind.FONT],
            "resources": [
                a
                for a in assets
                if a.kind in (BrandAsset.Kind.TEMPLATE, BrandAsset.Kind.GUIDELINE, BrandAsset.Kind.EXAMPLE)
            ],
            "stages": stages,
            "awaiting": [s for s in stages if s.status == BrandStage.Status.AWAITING_SIGNOFF],
            "can_approve": portal_permissions(request).get("portal_approve", False),
            "stage_files": {s.pk: [a for a in assets if a.stage_id == s.pk] for s in stages},
        },
    )


def _stage(request, stage_id):
    return get_object_or_404(
        BrandStage.objects.filter(workspace=request.portal_workspace, status=BrandStage.Status.AWAITING_SIGNOFF),
        pk=stage_id,
    )


@portal_permission_required("portal_approve")
@require_POST
def stage_sign_off(request, stage_id):
    stage = _stage(request, stage_id)
    sign_off(stage, request.user, request.POST.get("comment", "").strip()[:2000])
    messages.success(request, _("Thank you. %(stage)s is signed off.") % {"stage": stage.get_key_display()})
    return redirect("brand_portal:hub")


@portal_permission_required("portal_approve")
@require_POST
def stage_changes(request, stage_id):
    stage = _stage(request, stage_id)
    comment = request.POST.get("comment", "").strip()[:2000]
    if not comment:
        messages.error(request, _("Tell the studio what you'd like changed."))
        return redirect(reverse("brand_portal:hub") + "#signoff")
    request_changes(stage, request.user, comment)
    messages.success(request, _("Thanks. The studio has your notes and will come back with a new version."))
    return redirect("brand_portal:hub")


@portal_permission_required("portal_view_content")
@require_GET
def download(request, asset_id):
    asset = get_object_or_404(BrandAsset.objects.filter(workspace=request.portal_workspace), pk=asset_id)
    if not asset.client_can_see():
        raise Http404
    return redirect(asset.signed_url(inline=request.GET.get("inline") == "1"))
