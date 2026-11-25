"""Results and reports: figures, goals, the monthly report flow, sharing, export, scope and language.

The phase is done when a monthly report can be reviewed, approved, shared and exported
(``test_a_monthly_report_is_reviewed_approved_shared_and_exported``).
"""

import re
import zlib
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.analytics.models import AccountInsightsSnapshot, PostInsightsSnapshot
from apps.audit.models import AuditEvent
from apps.members.models import WorkspaceMembership
from apps.reports import ai, catalog, figures, services
from apps.reports.models import ClientGoal, ImportedMetric, Report
from apps.studio.permissions import ensure_client_roles
from apps.studio.tests.helpers import make_account, make_post, make_studio

SECRET = "Margin is thin this quarter"


@pytest.fixture
def studio(db):
    people = make_studio()
    roles = ensure_client_roles(people["org"])
    for key in ("restaurant_client", "skincare_client"):
        WorkspaceMembership.objects.filter(user=people[key]).update(custom_role=roles["Client admin"])
    people["ig"] = make_account(people["restaurant"], "Casa IG", "instagram")
    people["fb"] = make_account(people["restaurant"], "Casa FB", "facebook")
    people["tt"] = make_account(people["skincare"], "Luma TT", "tiktok")
    return people


def _daily(account, key, start, days, value):
    AccountInsightsSnapshot.objects.bulk_create(
        AccountInsightsSnapshot(social_account=account, metric_key=key, date=start + timedelta(days=i), value=value)
        for i in range(days)
    )


OCT = figures.month_period(2026, 10)
SEP = figures.month_period(2026, 9)


def _october(studio, views=100, reach=40):
    for acct in (studio["ig"], studio["fb"]):
        _daily(acct, "views", SEP.start, 61, views)
        _daily(acct, "reach", SEP.start, 61, reach)
    _daily(studio["ig"], "likes", OCT.start, 31, 5)
    _daily(studio["ig"], "comments", OCT.start, 31, 1)


def _portal(client, user, ws):
    client.force_login(user)
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(ws.id)
    session.save()


def _pdf_text(data: bytes) -> str:
    out = []
    for raw in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            out.append(zlib.decompress(raw).decode("latin-1"))
        except zlib.error:
            out.append(raw.decode("latin-1", "ignore"))
    return "\n".join(out)


# ── Catalog ──────────────────────────────────────────────────────────────────


def test_every_metric_says_where_it_comes_from_on_each_platform():
    for m in catalog.METRICS.values():
        assert str(m.definition)
        for platform in catalog.PLATFORMS:
            src = m.sources[platform]
            assert src.missing or src.field, (m.key, platform)
    # Instagram builds on views, never the deprecated impressions or plays.
    assert "impressions" not in catalog.METRICS["views"].sources["instagram"].keys
    assert catalog.METRICS["unique_viewers"].sources["facebook"].field == "page_total_media_view_unique"


# ── Figures ──────────────────────────────────────────────────────────────────


def test_a_platform_figure_sums_the_period_and_compares_with_the_previous_one(studio):
    _october(studio)
    fig = figures.figure(studio["restaurant"], "views", "instagram", OCT)
    assert fig.value == 3100 and fig.previous == 3000
    assert fig.change_pct == pytest.approx(3.3)
    assert fig.sources == ["Instagram: views"] and fig.updated_at
    assert len(fig.series) == 31


def test_totals_across_platforms_are_labelled_combined(studio):
    _october(studio)
    fig = figures.figure(studio["restaurant"], "views", "all", OCT)
    assert fig.value == 6200 and fig.combined
    assert figures.COMBINED_NOTE in fig.notes


def test_unique_viewers_are_never_added_across_platforms(studio):
    _october(studio)
    fig = figures.figure(studio["restaurant"], "unique_viewers", "all", OCT)
    assert not fig.available and fig.value is None
    assert fig.missing == figures.NEVER_COMBINED_NOTE


def test_a_metric_only_one_platform_gives_is_shown_as_that_platforms(studio):
    _october(studio)
    _daily(studio["ig"], "saves", OCT.start, 31, 2)
    fig = figures.figure(studio["restaurant"], "saves", "all", OCT)
    assert fig.value == 62 and not fig.combined
    assert "Instagram only." in fig.notes


def test_a_missing_metric_says_why_instead_of_zero(studio):
    fig = figures.figure(studio["restaurant"], "saves", "facebook", OCT)
    assert not fig.available and fig.value is None
    assert fig.missing == str(catalog.NOT_FROM_PLATFORM)
    tt = figures.figure(studio["skincare"], "unique_viewers", "tiktok", OCT)
    assert tt.missing == str(catalog.NOT_FROM_PLATFORM)
    assert figures.figure(studio["restaurant"], "profile_activity", "instagram", OCT).missing == str(
        catalog.NOT_COLLECTED
    )


def test_no_comparison_when_the_previous_period_has_little_data(studio):
    _daily(studio["ig"], "views", OCT.start - timedelta(days=5), 36, 100)
    fig = figures.figure(studio["restaurant"], "views", "instagram", OCT)
    assert fig.value == 3100 and fig.previous is None
    assert figures.PARTIAL_PREVIOUS_NOTE in fig.notes


def test_tiktok_daily_views_are_calculated_from_lifetime_totals(studio):
    post = make_post(studio["skincare"], studio["tt"], status="published", when=timezone.now() - timedelta(days=3))
    pp = post.platform_posts.get()
    pp.published_at = timezone.now() - timedelta(days=3)
    pp.save(update_fields=["published_at"])
    today = timezone.localdate()
    for i, total in enumerate((100, 250, 400)):
        PostInsightsSnapshot.objects.create(
            platform_post=pp, metric_key="views", date=today - timedelta(days=2 - i), value=total
        )
    fig = figures.figure(studio["skincare"], "views", "tiktok", figures.last_days(7))
    assert fig.calculated and fig.value == 400
    assert figures.CALCULATED_NOTE in fig.notes


def test_engagement_rate_is_engagement_over_views(studio):
    _october(studio)
    fig = figures.figure(studio["restaurant"], "engagement_rate", "instagram", OCT)
    assert fig.value == round(31 * 6 / 3100 * 100, 2) and fig.calculated


def test_facebook_reach_change_is_marked_on_the_trend(studio):
    _daily(studio["fb"], "reach", date(2026, 5, 1), 90, 10)
    fig = figures.figure(studio["restaurant"], "unique_viewers", "facebook", figures.month_period(2026, 6))
    assert any("15 June 2026" in n for n in fig.notes)
    assert fig.previous is None  # never one continuous line across the change


def test_imported_figures_fill_gaps_and_are_labelled(studio):
    ImportedMetric.objects.create(workspace=studio["skincare"], social_account=studio["tt"], metric="views",
                                  date=OCT.start, value=900)  # fmt: skip
    fig = figures.figure(studio["skincare"], "views", "tiktok", OCT)
    assert fig.value == 900 and fig.imported


def test_published_posts_rank_and_break_down(studio):
    ws = studio["restaurant"]
    for i, (title, reach) in enumerate((("Reel A", 900), ("Photo B", 300), ("Reel C", 600))):
        post = make_post(ws, studio["ig"], status="published", title=title, when=timezone.now() - timedelta(days=2 + i))
        pp = post.platform_posts.get()
        pp.published_at = timezone.now() - timedelta(days=2 + i)
        pp.save(update_fields=["published_at"])
        for key, value in (("views", reach * 2), ("likes", reach // 10)):
            PostInsightsSnapshot.objects.create(
                platform_post=pp, metric_key=key, date=timezone.localdate(), value=value
            )
    posts = figures.published(ws, figures.last_days(14))
    top = figures.rank(posts, "views")
    assert [p.title for p in top] == ["Reel A", "Reel C", "Photo B"]
    assert top[0].metrics["saves"] is None and top[0].missing.get("saves")
    groups = {b["by"]: b for b in figures.breakdowns(posts, "views")}
    assert set(groups) == {"pillar", "format", "hook", "length", "time_of_day"}
    assert groups["pillar"]["rows"][0]["posts"] == 3 and not groups["pillar"]["rows"][0]["small"]


# ── Goals ────────────────────────────────────────────────────────────────────


def test_goal_progress_against_the_pace_of_the_month(studio):
    _october(studio)
    ws = studio["restaurant"]
    goal = ClientGoal.objects.create(workspace=ws, name="Views", metric="views", platform="all", target=Decimal("6000"))
    assert figures.goal_progress(goal, today=date(2026, 10, 31)).status == "met"
    behind = ClientGoal.objects.create(
        workspace=ws, name="Big", metric="views", platform="all", target=Decimal("90000")
    )
    assert figures.goal_progress(behind, today=date(2026, 10, 15)).status == "behind"
    none = ClientGoal.objects.create(workspace=ws, name="Saves FB", metric="saves", platform="facebook", target=10)
    assert figures.goal_progress(none, today=date(2026, 10, 15)).status == "no_data"


def test_goals_show_in_client_health(studio):
    from apps.clients.health import goals_signal

    _october(studio)
    ws = studio["restaurant"]
    assert goals_signal(ws, date(2026, 10, 20)) is None
    ClientGoal.objects.create(workspace=ws, name="Huge", metric="views", platform="all", target=Decimal("900000"))
    signal = goals_signal(ws, date(2026, 10, 20))
    assert signal.tone == "warning" and "Huge" in signal.detail


# ── The monthly report ───────────────────────────────────────────────────────


def _report(studio, **kw):
    _october(studio)
    report = services.create_report(studio["restaurant"], 2026, 10, studio["editor"], **kw)
    report.internal_notes = SECRET
    report.save()
    return report


def test_a_monthly_report_is_reviewed_approved_shared_and_exported(studio, client, django_capture_on_commit_callbacks):
    report = _report(studio)
    ws = studio["restaurant"]
    services.draft_summary(report, studio["editor"])
    report.refresh_from_db()
    assert report.what_happened and report.ai_provider == "mock" and report.ai_warnings == []

    # Reviewed: sent for approval; approvers are told.
    with django_capture_on_commit_callbacks(execute=True):
        services.submit(report, studio["editor"])
    assert report.__class__.objects.get(pk=report.pk).status == "in_review"
    # An editor cannot approve; a manager can.
    client.force_login(studio["editor"])

    def url(action):
        return reverse("reports:action", kwargs={"workspace_id": ws.id, "report_id": report.id, "action": action})

    assert client.post(url("approve")).status_code == 403
    client.force_login(studio["manager"])
    client.post(url("approve"))
    report.refresh_from_db()
    assert report.status == "approved" and report.approved_by == studio["manager"]
    frozen = report.data["computed_at"]
    with pytest.raises(services.ReportError):
        services.refresh(report)

    # Shared: the client is emailed and reads it in the portal.
    with django_capture_on_commit_callbacks(execute=True):
        client.post(url("share"))
    report.refresh_from_db()
    assert report.status == "shared" and report.data["computed_at"] == frozen
    assert any(studio["restaurant_client"].email in m.to for m in mail.outbox)
    _portal(client, studio["restaurant_client"], ws)
    page = client.get(reverse("reports_portal:report", kwargs={"report_id": report.id}))
    assert page.status_code == 200
    body = page.content.decode()
    assert report.what_happened.split(".")[0] in body and SECRET not in body

    # Exported: a PDF without internal notes.
    pdf = client.get(reverse("reports_portal:pdf", kwargs={"report_id": report.id}))
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    text = _pdf_text(pdf.content)
    assert "What happened" in text and SECRET not in text

    # Sent as a link, which can be turned off.
    client.force_login(studio["manager"])
    client.post(url("link"))
    report.refresh_from_db()
    public = reverse("reports_public:report", kwargs={"token": report.share_token})
    client.logout()
    resp = client.get(public)
    assert resp.status_code == 200 and SECRET not in resp.content.decode()
    client.force_login(studio["manager"])
    client.post(url("revoke"))
    client.logout()
    assert client.get(public).status_code == 404
    verbs = set(AuditEvent.objects.filter(workspace=ws).values_list("verb", flat=True))
    assert {"report.created", "report.drafted", "report.submitted", "report.approved", "report.shared",
            "report.link_created", "report.link_revoked"} <= verbs  # fmt: skip


def test_drafts_and_reports_in_review_never_reach_the_portal(studio, client):
    report = _report(studio)
    report.what_happened = report.next_steps = "Done."
    report.save()
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    for status in ("draft", "in_review", "approved"):
        Report.objects.filter(pk=report.pk).update(status=status)
        assert client.get(reverse("reports_portal:report", kwargs={"report_id": report.id})).status_code == 404
        assert report.title not in client.get(reverse("reports_portal:index")).content.decode()


def test_a_report_needs_a_summary_before_review_and_can_go_back_to_draft(studio):
    report = _report(studio)
    with pytest.raises(services.ReportError, match="Write the summary"):
        services.submit(report, studio["editor"])
    report.what_happened = report.next_steps = "Text."
    report.save()
    services.submit(report, studio["editor"])
    services.send_back(report, studio["manager"], "Add the shoot.")
    report.refresh_from_db()
    assert report.status == "draft"


def test_internal_notes_stay_out_of_everything_the_client_sees(studio):
    from apps.reports.pdf import render_report
    from apps.reports.presentation import present

    report = _report(studio)
    assert SECRET not in str(present(report))
    assert SECRET not in str(report.data)
    assert SECRET not in str(ai.facts(report))
    assert SECRET not in _pdf_text(render_report(report))


def test_highlights_pick_the_posts_shown(studio):
    report = _report(studio)
    post = make_post(studio["restaurant"], studio["ig"], status="published", title="Pick me",
                     when=timezone.make_aware(timezone.datetime(2026, 10, 10, 12)))  # fmt: skip
    pp = post.platform_posts.get()
    pp.published_at = timezone.make_aware(timezone.datetime(2026, 10, 10, 12))
    pp.save(update_fields=["published_at"])
    services.update_text(report, studio["editor"], highlights=[str(pp.id)])
    assert [p["title"] for p in report.data["top_posts"]] == ["Pick me"]


# ── AI drafting ──────────────────────────────────────────────────────────────


def test_numbers_not_in_the_figures_are_flagged(studio):
    report = _report(studio)
    draft = ai.Draft("Views rose to 987,654 this month.", "Reels did well.", "Post more.", "test")
    warnings = ai.check_numbers(draft, ai.facts(report))
    assert warnings and "987,654" in warnings[0]
    real = ai.MockWriter().draft(ai.facts(report))
    assert ai.check_numbers(real, ai.facts(report)) == []


def test_the_claude_writer_uses_only_the_report_figures(studio, settings):
    settings.AI_PROVIDER = "claude"
    report = _report(studio)
    response = MagicMock(stop_reason="end_turn", model="claude-opus-5-5")
    response.content = [MagicMock(type="text", text='{"what_happened": "A", "why": "B", "next_steps": "C"}')]
    with patch("anthropic.Anthropic") as anthropic:
        anthropic.return_value.beta.messages.create.return_value = response
        services.draft_summary(report, studio["editor"])
        kwargs = anthropic.return_value.beta.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert SECRET not in kwargs["messages"][0]["content"]
    assert "Never invent" in kwargs["system"]
    report.refresh_from_db()
    assert report.what_happened == "A" and report.ai_provider.startswith("claude")


def test_a_refusal_or_drafting_switched_off_is_a_plain_message(studio, settings):
    report = _report(studio)
    settings.AI_PROVIDER = "none"
    with pytest.raises(services.ReportError, match="switched off"):
        services.draft_summary(report, studio["editor"])
    settings.AI_PROVIDER = "claude"
    with patch("anthropic.Anthropic") as anthropic:
        anthropic.return_value.beta.messages.create.return_value = MagicMock(stop_reason="refusal", content=[])
        with pytest.raises(services.ReportError, match="declined"):
            services.draft_summary(report, studio["editor"])


def test_the_draft_is_written_in_the_clients_portal_language(studio):
    from apps.clients.services import profile_for

    profile = profile_for(studio["restaurant"])
    profile.portal_language = "es"
    profile.save()
    report = _report(studio)
    services.draft_summary(report, studio["editor"])
    report.refresh_from_db()
    assert "publicaron" in report.what_happened
    assert "octubre" in report.title.lower()


# ── Pages, permissions and scope ─────────────────────────────────────────────


def test_studio_pages_render(studio, client):
    report = _report(studio)
    ws = studio["restaurant"].id
    client.force_login(studio["owner"])
    for url in (
        reverse("reports:dashboard", kwargs={"workspace_id": ws}),
        reverse("reports:dashboard", kwargs={"workspace_id": ws}) + "?platform=instagram&range=last_month&rank=views",
        reverse("reports:goals", kwargs={"workspace_id": ws}),
        reverse("reports:list", kwargs={"workspace_id": ws}),
        reverse("reports:report", kwargs={"workspace_id": ws, "report_id": report.id}),
        reverse("reports:pdf", kwargs={"workspace_id": ws, "report_id": report.id}),
        reverse("reports:import", kwargs={"workspace_id": ws}),
        reverse("reports:all"),
    ):
        assert client.get(url).status_code == 200, url


def test_goals_need_manage_reports(studio, client):
    ws = studio["restaurant"].id
    data = {"name": "Views", "metric": "views", "platform": "all", "target": "5000", "period": "monthly"}
    client.force_login(studio["viewer"])
    assert client.post(reverse("reports:goals", kwargs={"workspace_id": ws}), data).status_code == 403
    client.force_login(studio["editor"])
    client.post(reverse("reports:goals", kwargs={"workspace_id": ws}), data)
    assert ClientGoal.objects.filter(workspace=studio["restaurant"], name="Views").exists()


def test_another_clients_report_is_not_reachable_by_changing_the_url(studio, client):
    _october(studio)
    luma = services.create_report(studio["skincare"], 2026, 10, studio["owner"])
    Report.objects.filter(pk=luma.pk).update(status="shared")
    restaurant = studio["restaurant"].id
    client.force_login(studio["manager"])  # works on the restaurant only
    for url in (
        reverse("reports:report", kwargs={"workspace_id": restaurant, "report_id": luma.id}),
        reverse("reports:pdf", kwargs={"workspace_id": restaurant, "report_id": luma.id}),
    ):
        assert client.get(url).status_code == 404
    assert client.post(reverse("reports:action", kwargs={"workspace_id": restaurant, "report_id": luma.id,
                                                        "action": "approve"})).status_code == 404  # fmt: skip
    assert client.get(reverse("reports:dashboard", kwargs={"workspace_id": studio["skincare"].id})).status_code in (
        403,
        404,
    )
    assert "Luma" not in client.get(reverse("reports:all")).content.decode()
    # The restaurant's client can't open Luma's report in the portal either.
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    assert client.get(reverse("reports_portal:report", kwargs={"report_id": luma.id})).status_code == 404
    assert client.get(reverse("reports_portal:pdf", kwargs={"report_id": luma.id})).status_code == 404


def test_clients_never_reach_studio_results(studio, client):
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    resp = client.get(reverse("reports:dashboard", kwargs={"workspace_id": studio["restaurant"].id}))
    assert resp.status_code in (302, 403, 404)


def test_portal_results_need_the_reports_permission(studio, client):
    roles = ensure_client_roles(studio["org"])
    WorkspaceMembership.objects.filter(user=studio["restaurant_client"]).update(custom_role=roles["Client reviewer"])
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    assert client.get(reverse("reports_portal:index")).status_code == 403


def test_portal_results_show_published_work_in_spanish(studio, client):
    from apps.studio.languages import set_language

    post = make_post(studio["restaurant"], studio["ig"], status="published", title="Hudut Sunday",
                     when=timezone.now() - timedelta(days=3))  # fmt: skip
    pp = post.platform_posts.get()
    pp.published_at = timezone.now() - timedelta(days=3)
    pp.save(update_fields=["published_at"])
    PostInsightsSnapshot.objects.create(platform_post=pp, metric_key="views", date=timezone.localdate(), value=1234)
    set_language(studio["restaurant_client"], "es")
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    body = client.get(reverse("reports_portal:index")).content.decode()
    assert "Resultados" in body and "Hudut Sunday" in body and "1,234" in body
    assert client.get(reverse("client_portal:reports")).url == reverse("reports_portal:index")


def test_csv_import_adds_labelled_figures(studio, client):
    client.force_login(studio["owner"])
    ws = studio["skincare"]
    data = b"date,metric,value\n2026-10-01,views,1840\n2026-10-02,views,not-a-number\n2026-10-03,bogus,5\n"
    resp = client.post(reverse("reports:import", kwargs={"workspace_id": ws.id}),
                       {"account": str(studio["tt"].id), "file": SimpleUploadedFile("tiktok.csv", data)})  # fmt: skip
    assert resp.status_code == 302
    rows = list(ImportedMetric.objects.filter(social_account=studio["tt"]))
    assert len(rows) == 1 and rows[0].value == 1840 and rows[0].source_name == "tiktok.csv"
    bad = client.post(reverse("reports:import", kwargs={"workspace_id": ws.id}),
                      {"account": str(studio["tt"].id), "file": SimpleUploadedFile("x.csv", b"day,count\n")})  # fmt: skip
    assert bad.status_code == 302 and ImportedMetric.objects.count() == 1


def test_today_lists_reports_to_approve(studio, client):
    report = _report(studio)
    report.what_happened = report.next_steps = "Text."
    report.save()
    services.submit(report, studio["editor"])
    client.force_login(studio["owner"])
    assert "Reports to approve" in client.get(reverse("studio:today")).content.decode()
