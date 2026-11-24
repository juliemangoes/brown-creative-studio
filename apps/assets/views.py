"""Usage rights for a media asset, and everywhere it has been used."""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.assets.models import AssetRights
from apps.audit.services import record
from apps.clients.forms import _style
from apps.media_library.models import MediaAsset
from apps.members.decorators import require_permission
from apps.studio.decorators import in_local_time
from apps.studio.permissions import workspace_permission


class RightsForm(forms.ModelForm):
    class Meta:
        model = AssetRights
        fields = ["source", "usage", "licence", "talent_release", "release_reference", "expires_on"]
        widgets = {"expires_on": forms.DateInput(attrs={"type": "date"}), "licence": forms.Textarea(attrs={"rows": 3})}
        labels = {"expires_on": "Rights end on", "talent_release": "Everyone recognisable has signed a release"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


def where_used(asset):
    """Posts (with their status per platform) and shots that use this asset."""
    from apps.composer.models import PostMedia

    posts = []
    for pm in (
        PostMedia.objects.filter(media_asset=asset)
        .select_related("post")
        .prefetch_related("post__platform_posts__social_account")
    ):
        posts.append({"post": pm.post, "platforms": list(pm.post.platform_posts.all())})
    return {"posts": posts, "shots": list(asset.shots.select_related("shoot"))}


@require_permission("view_plan")
@in_local_time
@require_http_methods(["GET", "POST"])
def rights(request, workspace_id, asset_id):
    asset = get_object_or_404(MediaAsset.objects.filter(workspace=request.workspace), pk=asset_id)
    instance = AssetRights.objects.filter(media_asset=asset).first() or AssetRights(media_asset=asset)
    form = RightsForm(request.POST or None, instance=instance)
    can_edit = workspace_permission(request, "edit_media")
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if form.is_valid():
            saved = form.save(commit=False)
            saved.updated_by = request.user
            saved.save()
            record(
                "asset.rights_changed",
                f"Usage rights for {asset.filename} updated",
                workspace=request.workspace,
                actor=request.user,
                target=asset,
                data={"expires_on": str(saved.expires_on or "")},
            )
            messages.success(request, "Usage rights saved.")
            return redirect("assets:rights", workspace_id=workspace_id, asset_id=asset.id)
    usage = where_used(asset)
    upcoming_after_expiry = []
    if instance.pk and instance.expires_on:
        for row in usage["posts"]:
            when = row["post"].scheduled_at or row["post"].proposed_publish_at
            if (
                when
                and timezone.localtime(when).date() > instance.expires_on
                and not any(pp.status == "published" for pp in row["platforms"])
            ):
                upcoming_after_expiry.append(row["post"])
    return render(
        request,
        "assets/rights.html",
        {
            "workspace": request.workspace,
            "asset": asset,
            "form": form,
            "rights": instance if instance.pk else None,
            "usage": usage,
            "at_risk": upcoming_after_expiry,
            "can_edit": can_edit,
        },
    )
