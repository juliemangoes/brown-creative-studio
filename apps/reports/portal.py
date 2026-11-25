"""Results in the client portal: published work with its figures, and the reports the studio shared.

Needs the portal permission ``portal_view_reports`` (client admins by default).
Only reports in "Shared with the client" appear; drafts, reports waiting for
approval and the studio's internal notes never do. The share link shows one
report read-only to anyone who has it, until the studio turns it off.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET

from apps.reports import catalog, figures
from apps.reports.models import Report
from apps.studio.portal import (
    PortalNotice,
    PortalSection,
    portal_permission_required,
    portal_permissions,
    register_notice,
    register_section,
)

KEY = "portal_view_reports"
GALLERY_DAYS = 90


def shared_reports(workspace):
    return Report.objects.filter(workspace=workspace, status=Report.Status.SHARED)


@register_section
def _section(request):
    if portal_permissions(request).get(KEY):
        return PortalSection("reports_portal", gettext_lazy("Results"), reverse("reports_portal:index"), order=12)
    return None


@register_notice
def _new_report(request):
    if not portal_permissions(request).get(KEY) or getattr(request.resolver_match, "namespace", "") == "reports_portal":
        return None
    recent = shared_reports(request.portal_workspace).filter(shared_at__gte=timezone.now() - timedelta(days=14)).first()
    if recent is None:
        return None
    return PortalNotice(
        _("Your report for %(period)s is ready.") % {"period": recent.title.split(" · ")[-1]},
        reverse("reports_portal:report", kwargs={"report_id": recent.id}),
        _("Read the report"),
    )


@portal_permission_required(KEY)
@require_GET
def index(request):
    ws = request.portal_workspace
    platform = request.GET.get("platform", "all")
    if platform not in ("all", *catalog.PLATFORMS):
        platform = "all"
    posts = figures.published(ws, figures.last_days(GALLERY_DAYS), platform)
    for post in posts:
        post.published_dt = datetime.fromisoformat(post.published_at)  # type: ignore[attr-defined]
    present = sorted({a.platform for a in figures.accounts(ws)})
    return render(
        request,
        "reports/portal/index.html",
        {
            "workspace": ws,
            "posts": posts,
            "reports": shared_reports(ws),
            "platform": platform,
            "platforms": [(p, catalog.PLATFORM_LABELS[p]) for p in present],
            "days": GALLERY_DAYS,
            "not_from_platform": catalog.NOT_FROM_PLATFORM,
        },
    )


@portal_permission_required(KEY)
@require_GET
def report(request, report_id):
    r = get_object_or_404(shared_reports(request.portal_workspace), pk=report_id)
    return render(request, "reports/portal/report.html", {"workspace": r.workspace, "report": r, **view_context(r)})


@portal_permission_required(KEY)
@require_GET
def report_pdf(request, report_id):
    r = get_object_or_404(shared_reports(request.portal_workspace), pk=report_id)
    return pdf_response(r)


# ── Public share link ────────────────────────────────────────────────────────


@require_GET
def public_report(request, token):
    r = _by_token(token)
    return render(request, "reports/public_report.html", {"workspace": r.workspace, "report": r, **view_context(r)})


@require_GET
def public_pdf(request, token):
    return pdf_response(_by_token(token))


def _by_token(token):
    if not token or len(token) < 20:
        raise Http404
    r = Report.objects.select_related("workspace").filter(share_token=token).first()
    if r is None or r.status not in (Report.Status.APPROVED, Report.Status.SHARED):
        raise Http404
    return r


# ── Shared by every view of a report ─────────────────────────────────────────


def view_context(report) -> dict:
    """What a report page needs, from the frozen figures. Never includes internal notes."""
    from apps.reports.presentation import present

    return present(report)


def pdf_response(report) -> HttpResponse:
    from apps.reports.pdf import render_report

    response = HttpResponse(render_report(report), content_type="application/pdf")
    name = report.title.replace(" · ", " ").replace("/", "-")
    response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
    return response
