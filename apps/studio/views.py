"""Today, and signed downloads of private client files."""

from __future__ import annotations

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET

from apps.members.models import WorkspaceMembership
from apps.studio.decorators import in_studio_time, studio_staff_required
from apps.studio.files import PrivateFile, load_signed
from apps.studio.permissions import is_client_membership
from apps.studio.today import build_today, stall_days


@studio_staff_required
@in_studio_time
def today(request, workspace_id=None):
    selected = None
    if workspace_id is not None:
        # BrightBean's RBAC middleware already refused non-members; clients never get a studio Today.
        if is_client_membership(request.workspace_membership):
            raise PermissionDenied
        selected = request.workspace
    data = build_today(request.user, request.org, selected)
    return render(
        request,
        "studio/today.html",
        {
            "today": data,
            "now": timezone.now(),
            "today_date": timezone.localdate(),
            "stall_days": stall_days(),
            "studio_tz": settings.STUDIO_TIME_ZONE,
        },
    )


@login_required
@require_GET
def private_file(request, token):
    """Serve a private client file from a signed, short-lived link, after checking access again."""
    try:
        data = load_signed(token)
    except signing.SignatureExpired:
        return render(request, "studio/file_link_expired.html", status=410)
    except signing.BadSignature as exc:
        raise Http404 from exc
    try:
        model = django_apps.get_model(data["m"])
    except (LookupError, ValueError, KeyError) as exc:
        raise Http404 from exc
    if not issubclass(model, PrivateFile):
        raise Http404
    obj = get_object_or_404(model, pk=data["pk"])
    membership = (
        WorkspaceMembership.objects.filter(user=request.user, workspace_id=obj.workspace_id)
        .select_related("custom_role")
        .first()
    )
    if membership is None:
        raise PermissionDenied
    perms = membership.effective_permissions
    if is_client_membership(membership):
        if not (obj.client_can_see() and perms.get(model.PORTAL_PERMISSION, False)):
            raise PermissionDenied
    elif not perms.get(model.STAFF_PERMISSION, False):
        raise PermissionDenied

    inline = bool(data.get("i"))
    disposition = content_disposition_header(not inline, obj.download_name())
    storage = obj.file.storage
    if storage.__class__.__module__.startswith("storages.backends.s3"):
        url = storage.url(
            obj.file.name,
            parameters={"ResponseContentDisposition": disposition, "ResponseContentType": obj.mime_type},
            expire=60,
        )
        return HttpResponseRedirect(url)
    response = FileResponse(obj.file.open("rb"), content_type=obj.mime_type)
    response["Content-Disposition"] = disposition
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    response["Cache-Control"] = "private, no-store"
    return response
