"""Monthly reports: create, refresh figures, draft the summary, review, approve, share, export.

Draft → Waiting for approval → Approved → Shared with the client. A report can
be sent back to draft until it is shared. Approving freezes its figures.
Every step is in the audit log; the client is told when a report is shared.
"""

from __future__ import annotations

import logging
from datetime import date

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.audit.services import record
from apps.reports import ai, figures
from apps.reports.models import Report

log = logging.getLogger(__name__)

TOP_POSTS = 6


class ReportError(ValueError):
    """A report action that can't be done; the message is for the person."""


def default_title(workspace, period: figures.Period, language: str = "en") -> str:
    from django.utils import translation
    from django.utils.formats import date_format

    with translation.override(language):
        return f"{workspace.name} · {date_format(period.start, 'F Y').capitalize()}"


def create_report(workspace, year: int, month: int, actor, rank_by: str = "engagement") -> Report:
    from apps.studio.languages import user_language

    period = figures.month_period(year, month)
    if (
        Report.objects.filter(workspace=workspace, period_start=period.start, period_end=period.end)
        .exclude(status=Report.Status.DRAFT)
        .exists()
    ):
        raise ReportError("There is already a report for that month in review or shared. Open it instead.")
    lang = _client_language(workspace) or user_language(actor, workspace)
    report = Report.objects.create(
        workspace=workspace,
        title=default_title(workspace, period, lang),
        period_start=period.start,
        period_end=period.end,
        rank_by=rank_by,
        created_by=actor,
    )
    refresh(report)
    record("report.created", f"Report started: {report.title}.", workspace=workspace, actor=actor, target=report)
    return report


def _client_language(workspace) -> str:
    profile = getattr(workspace, "client_profile", None)
    return getattr(profile, "portal_language", "") or ""


def compute(report) -> dict:
    """The report's figures, everything the client may see. Nothing internal goes in here.

    Stored in English (labels are translated when shown: apps.reports.presentation).
    """
    from django.utils import translation

    with translation.override("en"):
        return _compute(report)


def _compute(report) -> dict:
    ws = report.workspace
    period = figures.Period(report.period_start, report.period_end)
    present = sorted({a.platform for a in figures.accounts(ws)})
    overview = {"all": [f.as_dict() for f in figures.overview(ws, "all", period)]}
    for platform in present:
        overview[platform] = [f.as_dict() for f in figures.overview(ws, platform, period)]
    posts = figures.published(ws, period)
    chosen = [p for p in posts if p.id in set(report.highlights or [])]
    top = chosen or figures.rank(posts, report.rank_by, TOP_POSTS)
    goals = []
    for g in figures.goals_progress(ws, today=min(period.end, timezone.localdate()), period=None):
        goals.append(
            {
                "name": g.goal.name,
                "metric": g.goal.metric,
                "platform": g.goal.platform,
                "target": g.target,
                "actual": g.actual,
                "pct": g.pct,
                "status": g.status,
                "label": g.label,
                "period": [g.period.start.isoformat(), g.period.end.isoformat()],
            }  # fmt: skip
        )
    return {
        "period": {"start": period.start.isoformat(), "end": period.end.isoformat()},
        "previous": {"start": period.previous().start.isoformat(), "end": period.previous().end.isoformat()},
        "platforms": present,
        "overview": overview,
        "published_count": len(posts),
        "rank_by": report.rank_by,
        "top_posts": [p.as_dict() for p in top],
        "breakdowns": figures.breakdowns(posts, report.rank_by),
        "goals": goals,
        "computed_at": timezone.now().isoformat(),
    }


def refresh(report) -> Report:
    if not report.is_editable:
        raise ReportError("Approved reports keep the figures they were approved with.")
    report.data = compute(report)
    report.computed_at = timezone.now()
    report.save(update_fields=["data", "computed_at", "updated_at"])
    return report


def draft_summary(report, actor) -> ai.Draft:
    if not report.is_editable:
        raise ReportError("Only a draft can be redrafted.")
    lang = _client_language(report.workspace) or "en"
    try:
        result = ai.draft_for(report, lang)
    except ai.DraftingError as exc:
        raise ReportError(str(exc)) from exc
    report.what_happened, report.why, report.next_steps = result.what_happened, result.why, result.next_steps
    report.ai_drafted_at, report.ai_provider, report.ai_warnings = timezone.now(), result.provider, result.warnings
    report.save(update_fields=["what_happened", "why", "next_steps", "ai_drafted_at", "ai_provider", "ai_warnings",
                               "updated_at"])  # fmt: skip
    record(
        "report.drafted",
        f"Summary drafted ({result.provider}): {report.title}.",
        workspace=report.workspace,
        actor=actor,
        target=report,
        data={"warnings": result.warnings},
    )
    return result


def update_text(report, actor, **fields) -> Report:
    if not report.is_editable:
        raise ReportError("This report is approved. Send it back to draft to change it.")
    allowed = {"title", "what_happened", "why", "next_steps", "internal_notes", "highlights", "rank_by"}
    changed = []
    for key, value in fields.items():
        if key in allowed and getattr(report, key) != value:
            setattr(report, key, value)
            changed.append(key)
    if changed:
        report.save(update_fields=[*changed, "updated_at"])
        if {"highlights", "rank_by"} & set(changed):
            refresh(report)
    return report


@transaction.atomic
def submit(report, actor) -> Report:
    report = Report.objects.select_for_update().get(pk=report.pk)
    if report.status != Report.Status.DRAFT:
        raise ReportError("Only a draft can be sent for approval.")
    if not (report.what_happened.strip() and report.next_steps.strip()):
        raise ReportError("Write the summary (what happened and what to do next) before sending it for approval.")
    report.data = compute(report)
    report.computed_at = timezone.now()
    report.status, report.submitted_by = Report.Status.IN_REVIEW, actor
    report.save()
    record("report.submitted", f"Report sent for approval: {report.title}.", workspace=report.workspace, actor=actor,
           target=report)  # fmt: skip
    transaction.on_commit(lambda: _notify_approvers(report, actor))
    return report


@transaction.atomic
def approve(report, actor) -> Report:
    report = Report.objects.select_for_update().get(pk=report.pk)
    if report.status != Report.Status.IN_REVIEW:
        raise ReportError("Only a report waiting for approval can be approved.")
    report.status, report.approved_by, report.approved_at = Report.Status.APPROVED, actor, timezone.now()
    report.save()
    record("report.approved", f"Report approved: {report.title}.", workspace=report.workspace, actor=actor,
           target=report)  # fmt: skip
    return report


@transaction.atomic
def send_back(report, actor, reason: str = "") -> Report:
    report = Report.objects.select_for_update().get(pk=report.pk)
    if report.status not in (Report.Status.IN_REVIEW, Report.Status.APPROVED):
        raise ReportError("Only a report in review or approved (not yet shared) can go back to draft.")
    report.status, report.approved_by, report.approved_at = Report.Status.DRAFT, None, None
    report.save()
    record("report.sent_back", f"Report sent back to draft: {report.title}." + (f" {reason}" if reason else ""),
           workspace=report.workspace, actor=actor, target=report)  # fmt: skip
    return report


@transaction.atomic
def share(report, actor) -> Report:
    report = Report.objects.select_for_update().get(pk=report.pk)
    if report.status != Report.Status.APPROVED:
        raise ReportError("A report has to be approved before the client sees it.")
    report.status, report.shared_at = Report.Status.SHARED, timezone.now()
    report.save()
    record("report.shared", f"Report shared with the client: {report.title}.", workspace=report.workspace,
           actor=actor, target=report)  # fmt: skip
    transaction.on_commit(lambda: _email_client(report))
    return report


def create_link(report, actor) -> str:
    if report.status not in (Report.Status.APPROVED, Report.Status.SHARED):
        raise ReportError("Only an approved report can be sent as a link.")
    report.new_share_token()
    report.save(update_fields=["share_token", "updated_at"])
    record("report.link_created", f"Share link created: {report.title}.", workspace=report.workspace, actor=actor,
           target=report)  # fmt: skip
    return report.share_token


def revoke_link(report, actor) -> None:
    report.share_token = ""
    report.save(update_fields=["share_token", "updated_at"])
    record("report.link_revoked", f"Share link turned off: {report.title}.", workspace=report.workspace, actor=actor,
           target=report)  # fmt: skip


def previous_months(today: date | None = None, count: int = 12) -> list[tuple[int, int]]:
    today = today or timezone.localdate()
    y, m = today.year, today.month
    out = []
    for _ in range(count):
        out.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def _notify_approvers(report, actor) -> None:
    from apps.members.models import WorkspaceMembership
    from apps.notifications.engine import notify

    for m in WorkspaceMembership.objects.filter(workspace=report.workspace).select_related("user", "custom_role"):
        if m.user_id == getattr(actor, "pk", None) or not m.effective_permissions.get("approve_reports", False):
            continue
        try:
            notify(
                m.user,
                "report_review",
                title=f"Report to approve: {report.title}",
                body=f"{actor.name or actor.email} sent it for approval.",
                data={
                    "url": reverse(
                        "reports:report", kwargs={"workspace_id": report.workspace_id, "report_id": report.id}
                    )
                },  # fmt: skip
            )
        except Exception:
            log.exception("Report review notification to %s failed", m.user_id)


def _email_client(report) -> None:
    from django.utils.translation import gettext

    from apps.studio.portal import client_users, send_portal_email

    for user in client_users(report.workspace, "portal_view_reports"):
        try:
            send_portal_email(
                user,
                report.workspace,
                subject=lambda: gettext("Your report is ready: %(title)s") % {"title": report.title},
                template="reports/email/report_shared",
                context={"report": report},
            )
        except Exception:
            log.exception("Report email to %s failed", user.pk)
