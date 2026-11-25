"""Results in the studio: the client dashboard, goals, monthly reports and CSV import.

Seeing results needs BrightBean's ``view_analytics``. Goals, reports and
imports need ``manage_reports``; approving and sharing need ``approve_reports``.
Every query is scoped to the workspace in the URL.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.audit.services import record
from apps.clients.forms import _style
from apps.members.decorators import require_permission
from apps.reports import catalog, figures, services
from apps.reports.models import ClientGoal, ImportedMetric, Report
from apps.reports.portal import pdf_response, view_context
from apps.studio.decorators import in_local_time, in_studio_time, studio_staff_required
from apps.studio.permissions import workspace_permission
from apps.studio.today import staff_memberships

RANGES = {
    "7": "Last 7 days",
    "30": "Last 30 days",
    "90": "Last 90 days",
    "this_month": "This month",
    "last_month": "Last month",
}


def _period(choice: str, today: date | None = None) -> figures.Period:
    today = today or timezone.localdate()
    if choice == "this_month":
        return figures.Period(today.replace(day=1), today)
    if choice == "last_month":
        first = today.replace(day=1)
        prev = figures.month_period(first.year - (first.month == 1), (first.month - 2) % 12 + 1)
        return prev
    return figures.last_days(int(choice) if choice in ("7", "30", "90") else 30, today)


def _can(request, key) -> bool:
    return workspace_permission(request, key)


def _require(request, key):
    if not _can(request, key):
        raise PermissionDenied(f"Permission denied: {key}")


# ── All clients ──────────────────────────────────────────────────────────────


@studio_staff_required
@in_studio_time
def all_reports(request):
    memberships = [
        m for m in staff_memberships(request.user, request.org) if m.effective_permissions.get("view_analytics", False)
    ]
    ids = [m.workspace_id for m in memberships]
    reports = Report.objects.filter(workspace_id__in=ids).select_related("workspace")[:100]
    waiting = [r for r in reports if r.status == Report.Status.IN_REVIEW]
    return render(
        request,
        "reports/all.html",
        {"reports": reports, "waiting": waiting, "clients": [m.workspace for m in memberships]},
    )


# ── One client: dashboard ────────────────────────────────────────────────────


@require_permission("view_analytics")
@in_local_time
@require_GET
def dashboard(request, workspace_id):
    ws = request.workspace
    platform = request.GET.get("platform", "all")
    present = sorted({a.platform for a in figures.accounts(ws)})
    if platform != "all" and platform not in present:
        platform = "all"
    choice = request.GET.get("range", "30")
    if choice not in RANGES:
        choice = "30"
    period = _period(choice)
    rank_by = request.GET.get("rank", "engagement")
    if rank_by not in catalog.RANK_METRICS:
        rank_by = "engagement"
    from apps.reports.presentation import chart, figure_view

    figs = [figure_view(f.as_dict()) for f in figures.overview(ws, platform, period)]
    posts = figures.published(ws, period, platform)
    views = next((f for f in figs if f["metric"] == "views"), None)
    return render(
        request,
        "reports/dashboard.html",
        {
            "workspace": ws,
            "platform": platform,
            "platforms": [("all", catalog.PLATFORM_LABELS["all"])] + [(p, catalog.PLATFORM_LABELS[p]) for p in present],
            "range": choice,
            "ranges": RANGES.items(),
            "period": period,
            "previous": period.previous(),
            "figures": figs,
            "chart": chart(views["series"]) if views and views["available"] else {},
            "break_notes": _breaks(platform, period, present),
            "rank_by": rank_by,
            "rank_choices": [(k, catalog.METRICS[k].label) for k in catalog.RANK_METRICS],
            "top": figures.rank(posts, rank_by, 8),
            "breakdowns": figures.breakdowns(posts, rank_by),
            "post_count": len(posts),
            "goals": figures.goals_progress(ws),
            "can_manage": _can(request, "manage_reports"),
            "rank_kind": catalog.METRICS[rank_by].kind,
        },
    )


def _breaks(platform, period, present) -> list[str]:
    notes = []
    for (p, _metric), (when, note) in catalog.BREAKS.items():
        if (platform in ("all", p)) and p in present and period.previous().start <= when <= period.end:
            notes.append(str(note))
    return notes


# ── Goals ────────────────────────────────────────────────────────────────────


class GoalForm(forms.ModelForm):
    class Meta:
        model = ClientGoal
        fields = ["name", "metric", "platform", "target", "period", "starts_on", "ends_on"]
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {"starts_on": "From", "ends_on": "To", "period": "Measured"}

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["metric"] = forms.ChoiceField(
            choices=[(k, catalog.METRICS[k].label) for k in catalog.GOAL_METRICS], label="Metric"
        )
        present = sorted({a.platform for a in figures.accounts(workspace)}) if workspace else list(catalog.PLATFORMS)
        self.fields["platform"] = forms.ChoiceField(
            choices=[("all", "All platforms")] + [(p, catalog.PLATFORM_LABELS[p]) for p in present], label="Platform"
        )
        self.fields["target"].help_text = "For engagement rate, a percentage (for example 4.5)."
        _style(self)

    def clean(self):
        data = super().clean()
        if data.get("period") == "custom":
            if not (data.get("starts_on") and data.get("ends_on")):
                self.add_error("ends_on", "Choose both dates.")
            elif data["ends_on"] < data["starts_on"]:
                self.add_error("ends_on", "The end is before the start.")
        if data.get("target") is not None and data["target"] <= 0:
            self.add_error("target", "Set a target above zero.")
        return data


@require_permission("view_analytics")
@in_local_time
@require_http_methods(["GET", "POST"])
def goals(request, workspace_id):
    ws = request.workspace
    form = GoalForm(workspace=ws)
    if request.method == "POST":
        _require(request, "manage_reports")
        form = GoalForm(request.POST, workspace=ws)
        if form.is_valid():
            goal = form.save(commit=False)
            goal.workspace, goal.created_by = ws, request.user
            goal.save()
            record("goal.created", f"Goal set: {goal.name}.", workspace=ws, target=goal)
            messages.success(request, "Goal added.")
            return redirect("reports:goals", workspace_id=ws.id)
    return render(
        request,
        "reports/goals.html",
        {
            "workspace": ws,
            "form": form,
            "progress": figures.goals_progress(ws),
            "can_manage": _can(request, "manage_reports"),
            "inactive": ClientGoal.objects.filter(workspace=ws, is_active=False),
        },  # fmt: skip
    )


@require_permission("manage_reports")
@require_POST
def goal_toggle(request, workspace_id, goal_id):
    goal = get_object_or_404(ClientGoal, pk=goal_id, workspace=request.workspace)
    goal.is_active = not goal.is_active
    goal.save(update_fields=["is_active", "updated_at"])
    record("goal.updated", f"Goal {'resumed' if goal.is_active else 'stopped'}: {goal.name}.",
           workspace=request.workspace, target=goal)  # fmt: skip
    return redirect("reports:goals", workspace_id=workspace_id)


# ── Reports ──────────────────────────────────────────────────────────────────


@require_permission("view_analytics")
@in_local_time
@require_GET
def report_list(request, workspace_id):
    ws = request.workspace
    months = [(f"{y}-{m:02d}", date(y, m, 1)) for y, m in services.previous_months()]
    return render(
        request,
        "reports/list.html",
        {
            "workspace": ws,
            "reports": Report.objects.filter(workspace=ws),
            "months": months,
            "can_manage": _can(request, "manage_reports"),
            "rank_choices": [(k, catalog.METRICS[k].label) for k in catalog.RANK_METRICS],
        },  # fmt: skip
    )


@require_permission("manage_reports")
@require_POST
def report_create(request, workspace_id):
    try:
        year, month = (int(x) for x in request.POST.get("month", "").split("-"))
        date(year, month, 1)
    except ValueError:
        messages.error(request, "Choose a month.")
        return redirect("reports:list", workspace_id=workspace_id)
    rank_by = request.POST.get("rank_by", "engagement")
    if rank_by not in catalog.RANK_METRICS:
        rank_by = "engagement"
    try:
        report = services.create_report(request.workspace, year, month, request.user, rank_by)
    except services.ReportError as exc:
        messages.error(request, str(exc))
        return redirect("reports:list", workspace_id=workspace_id)
    return redirect("reports:report", workspace_id=workspace_id, report_id=report.id)


def _report(request, report_id) -> Report:
    return get_object_or_404(Report.objects.select_related("workspace", "approved_by", "submitted_by"), pk=report_id,
                             workspace=request.workspace)  # fmt: skip


@require_permission("view_analytics")
@in_local_time
@require_http_methods(["GET", "POST"])
def report_detail(request, workspace_id, report_id):
    report = _report(request, report_id)
    if request.method == "POST":
        _require(request, "manage_reports")
        try:
            services.update_text(
                report,
                request.user,
                title=request.POST.get("title", report.title).strip()[:200] or report.title,
                what_happened=request.POST.get("what_happened", ""),
                why=request.POST.get("why", ""),
                next_steps=request.POST.get("next_steps", ""),
                internal_notes=request.POST.get("internal_notes", ""),
                rank_by=request.POST.get("rank_by", report.rank_by)
                if request.POST.get("rank_by") in catalog.RANK_METRICS
                else report.rank_by,
                highlights=request.POST.getlist("highlights") or report.highlights,
            )
        except services.ReportError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Saved.")
        return redirect("reports:report", workspace_id=workspace_id, report_id=report.id)
    period = figures.Period(report.period_start, report.period_end)
    candidates = figures.published(report.workspace, period) if report.is_editable else []
    share_url = (
        request.build_absolute_uri(reverse("reports_public:report", kwargs={"token": report.share_token}))
        if report.share_token
        else ""
    )
    return render(
        request,
        "reports/report.html",
        {
            "workspace": report.workspace,
            "report": report,
            **view_context(report),
            "candidates": candidates,
            "can_manage": _can(request, "manage_reports"),
            "can_approve": _can(request, "approve_reports"),
            "rank_choices": [(k, catalog.METRICS[k].label) for k in catalog.RANK_METRICS],
            "share_url": share_url,
            "ai_provider": services.ai.writer().name if services.ai.writer() else "",
        },
    )


@require_permission("manage_reports")
@require_POST
def report_action(request, workspace_id, report_id, action):
    report = _report(request, report_id)
    approver_actions = {"approve", "share", "link", "revoke"}
    if action in approver_actions:
        _require(request, "approve_reports")
    try:
        if action == "refresh":
            services.refresh(report)
            messages.success(request, "Figures updated.")
        elif action == "draft":
            result = services.draft_summary(report, request.user)
            messages.success(request, "Summary drafted. Read it and edit it before sending it for approval.")
            for w in result.warnings:
                messages.warning(request, w)
        elif action == "submit":
            services.submit(report, request.user)
            messages.success(request, "Sent for approval.")
        elif action == "approve":
            services.approve(report, request.user)
            messages.success(request, "Approved. The figures are now frozen.")
        elif action == "send_back":
            services.send_back(report, request.user, request.POST.get("reason", "").strip())
            messages.success(request, "Back to draft.")
        elif action == "share":
            services.share(report, request.user)
            messages.success(request, "Shared. The client can read it in their portal and has been emailed.")
        elif action == "link":
            services.create_link(report, request.user)
            messages.success(request, "Link created. Anyone with it can read this report until you turn it off.")
        elif action == "revoke":
            services.revoke_link(report, request.user)
            messages.success(request, "Link turned off.")
        elif action == "delete":
            if report.status != Report.Status.DRAFT:
                raise services.ReportError("Only a draft can be deleted.")
            title = report.title
            report.delete()
            record("report.deleted", f"Draft report deleted: {title}.", workspace=request.workspace)
            messages.success(request, "Draft deleted.")
            return redirect("reports:list", workspace_id=workspace_id)
        else:
            raise services.ReportError("Unknown action.")
    except services.ReportError as exc:
        messages.error(request, str(exc))
    return redirect("reports:report", workspace_id=workspace_id, report_id=report.id)


@require_permission("view_analytics")
@in_local_time
@require_GET
def report_pdf(request, workspace_id, report_id):
    return pdf_response(_report(request, report_id))


# ── CSV import ───────────────────────────────────────────────────────────────

IMPORT_METRICS = (
    "views",
    "engagement",
    "comments",
    "shares",
    "saves",
    "audience_growth",
    "unique_viewers",
    "link_clicks",
)


@require_permission("view_analytics")
@require_http_methods(["GET", "POST"])
def import_csv(request, workspace_id):
    ws = request.workspace
    accts = figures.accounts(ws)
    if request.method == "POST":
        _require(request, "manage_reports")
        account = next((a for a in accts if str(a.id) == request.POST.get("account")), None)
        upload = request.FILES.get("file")
        if account is None or upload is None:
            messages.error(request, "Choose an account and a CSV file.")
            return redirect("reports:import", workspace_id=ws.id)
        if upload.size > 2 * 1024 * 1024:
            messages.error(request, "That file is over 2 MB. Split it by month.")
            return redirect("reports:import", workspace_id=ws.id)
        try:
            count, skipped = _import_rows(account, upload, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("reports:import", workspace_id=ws.id)
        record("report.imported", f"Imported {count} figures for {account.account_name} from {upload.name}.",
               workspace=ws, data={"skipped": skipped})  # fmt: skip
        messages.success(
            request,
            f"Imported {count} figures." + (f" Skipped {skipped} rows (see the format below)." if skipped else ""),
        )
        return redirect("reports:import", workspace_id=ws.id)
    recent = ImportedMetric.objects.filter(workspace=ws).select_related("social_account").order_by("-imported_at")[:20]
    return render(request, "reports/import.html", {"workspace": ws, "accounts": accts, "recent": recent,
                                                   "metrics": IMPORT_METRICS, "can_manage": _can(request, "manage_reports")})  # fmt: skip


def _import_rows(account, upload, user) -> tuple[int, int]:
    """CSV with columns date (YYYY-MM-DD), metric, value. One row per account, metric and day."""
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Save the file as UTF-8 CSV and try again.") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    if not {"date", "metric", "value"} <= headers:
        raise ValueError("The first row must be: date, metric, value.")
    count = skipped = 0
    for row in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        try:
            day = date.fromisoformat(row["date"])
            metric = row["metric"].lower()
            value = float(row["value"].replace(",", ""))
        except (KeyError, ValueError):
            skipped += 1
            continue
        if metric not in IMPORT_METRICS or day > timezone.localdate():
            skipped += 1
            continue
        ImportedMetric.objects.update_or_create(
            social_account=account,
            metric=metric,
            date=day,
            defaults={
                "workspace": account.workspace,
                "value": value,
                "source_name": upload.name[:255],
                "imported_by": user,
            },
        )
        count += 1
    return count, skipped
