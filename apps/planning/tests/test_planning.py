"""Planning: production stages, ideas into the calendar, the 13-week plan, warnings, key dates, scope."""

from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.clients.models import Retainer
from apps.composer.models import ContentCategory, Idea, PostMedia
from apps.planning.models import Campaign, ContentPlan, KeyDate
from apps.planning.services import (
    belize_holidays,
    combined_label,
    ensure_holidays,
    plan_idea,
    plan_weeks,
    planning_warnings,
    quick_plan,
    set_stage,
    week_start,
)
from apps.reviews.history import post_history
from apps.studio.tests.helpers import make_account, make_post, make_studio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def studio(db):
    people = make_studio()
    people["account"] = make_account(people["restaurant"], "Casa Maya")
    people["luma_account"] = make_account(people["skincare"], "Luma")
    return people


def _asset(ws, name="photo.png"):
    from apps.media_library.models import MediaAsset

    return MediaAsset.objects.create(
        organization=ws.organization,
        workspace=ws,
        file=SimpleUploadedFile(name, PNG),
        filename=name,
        media_type="image",
        mime_type="image/png",
    )


def _today():
    return timezone.localdate()


# ── Plans and stages ─────────────────────────────────────────────────────────


def test_quick_plan_drafts_on_every_connected_account(studio):
    make_account(studio["restaurant"], "Old page", platform="facebook", status="disconnected")
    plan = quick_plan(studio["restaurant"], studio["editor"], title="Taco Tuesday", target_date=_today() + timedelta(9))
    post = plan.post
    assert plan.stage == ContentPlan.Stage.PLANNED and plan.owner == studio["editor"]
    assert list(post.platform_posts.values_list("social_account__account_name", "status")) == [("Casa Maya", "draft")]
    assert timezone.localtime(post.proposed_publish_at).date() == _today() + timedelta(9)
    assert AuditEvent.objects.filter(verb="production.planned", target_id=str(post.pk)).exists()


def test_stage_changes_are_audited_and_labelled_with_the_status(studio):
    plan = quick_plan(studio["restaurant"], studio["editor"], title="Brunch", target_date=_today() + timedelta(5))
    assert set_stage(plan, ContentPlan.Stage.SHOT_NEEDED, studio["editor"])
    assert not set_stage(plan, ContentPlan.Stage.SHOT_NEEDED, studio["editor"])  # no-op
    assert not set_stage(plan, "nonsense", studio["editor"])
    event = AuditEvent.objects.get(verb="production.stage_changed", target_id=str(plan.post.pk))
    assert event.summary == "Production stage: Planned → Shot needed"
    pp = plan.post.platform_posts.get()
    assert combined_label(pp) == "Shot needed · Draft"
    pp.status = "approved"
    assert combined_label(pp, plan=plan) == "Approved"  # once approved, production is done
    pp.status = "scheduled"
    assert combined_label(pp, plan=plan) == "Scheduled"


def test_an_idea_becomes_a_planned_post_in_one_action(studio):
    ws = studio["restaurant"]
    idea = Idea.objects.create(
        workspace=ws, author=studio["editor"], title="Chef's table", description="Rosa plating", tags=["chef"]
    )
    plan = plan_idea(idea, studio["editor"], target_date=_today() + timedelta(12))
    idea.refresh_from_db()
    assert idea.post == plan.post and plan.post.tags == ["chef"]
    assert plan.stage == ContentPlan.Stage.SHOT_NEEDED  # no media yet
    assert plan_idea(idea, studio["editor"], target_date=_today()).pk == plan.pk  # idempotent
    kinds = [e.kind for e in post_history(plan.post)]
    assert kinds[:2] == ["idea", "created"] or kinds[0] == "idea"


def test_an_idea_with_media_starts_as_planned(studio):
    ws = studio["restaurant"]
    idea = Idea.objects.create(workspace=ws, author=studio["editor"], title="Tamales", media_asset=_asset(ws))
    plan = plan_idea(idea, studio["editor"], target_date=_today() + timedelta(3))
    assert plan.stage == ContentPlan.Stage.PLANNED
    assert PostMedia.objects.filter(post=plan.post).count() == 1


# ── The plan by week ─────────────────────────────────────────────────────────


def test_weeks_hold_posts_key_dates_and_campaigns_for_this_client_only(studio):
    ws, other = studio["restaurant"], studio["skincare"]
    start = week_start(_today())
    quick_plan(ws, studio["editor"], title="Week one", target_date=start + timedelta(2))
    quick_plan(other, studio["owner"], title="Luma post", target_date=start + timedelta(2))
    KeyDate.objects.create(organization=studio["org"], date=start + timedelta(3), title="Studio-wide holiday")
    KeyDate.objects.create(organization=studio["org"], workspace=ws, date=start + timedelta(4), title="Anniversary")
    KeyDate.objects.create(organization=studio["org"], workspace=other, date=start + timedelta(4), title="Luma launch")
    Campaign.objects.create(
        workspace=ws, name="Christmas", starts_on=start + timedelta(8), ends_on=start + timedelta(20)
    )
    Retainer.objects.create(workspace=ws, posts_per_month=13)

    weeks = plan_weeks([ws], _today())
    assert len(weeks) == 13 and weeks[0].start == start
    assert [i.post.title for i in weeks[0].items] == ["Week one"]
    assert {k.title for k in weeks[0].key_dates} == {"Studio-wide holiday", "Anniversary"}
    assert [c.name for c in weeks[1].campaigns] == ["Christmas"] and [c.name for c in weeks[2].campaigns] == [
        "Christmas"
    ]
    assert weeks[0].expected == pytest.approx(3.0) and weeks[0].short  # 1 of about 3


def test_warnings_flag_gaps_one_sided_mix_missing_media_and_approval(studio):
    ws = studio["restaurant"]
    Retainer.objects.create(workspace=ws, posts_per_month=13)
    pillar = ContentCategory.objects.create(workspace=ws, name="Menu")
    today = _today()
    for offset in (1, 2, 4, 6):
        plan = quick_plan(
            ws, studio["editor"], title=f"Menu {offset}", target_date=today + timedelta(offset), pillar=pillar
        )
        plan.format = "reel"
        plan.save(update_fields=["format"])
    texts = [w.text for w in planning_warnings(ws, plan_weeks([ws], today), today)]
    assert any("share one pillar: Menu" in t for t in texts)
    assert any("share one format: Reel" in t for t in texts)
    assert any("“Menu 1”" in t and "no photo or video" in t for t in texts)
    assert any("“Menu 1”" in t and "isn't approved yet" in t for t in texts)
    assert any(t.startswith("Week of") for t in texts)


def test_rights_ending_before_the_post_date_are_flagged(studio):
    from apps.assets.models import AssetRights

    ws = studio["restaurant"]
    asset = _asset(ws, "rosa.jpg")
    AssetRights.objects.create(media_asset=asset, expires_on=_today() + timedelta(2))
    plan = quick_plan(
        ws, studio["editor"], title="Rosa", target_date=_today() + timedelta(10), media_asset_ids=[asset.id]
    )
    texts = [w.text for w in planning_warnings(ws, plan_weeks([ws], _today()), _today())]
    assert any("The rights to rosa.jpg end" in t and "“Rosa”" in t for t in texts), texts
    assert plan.post.title == "Rosa"


# ── Key dates ────────────────────────────────────────────────────────────────


def test_belize_holidays_include_the_moveable_easter_days():
    days = dict((title, day) for day, title in belize_holidays(2027))
    assert days["Good Friday"] == date(2027, 3, 26) and days["Easter Monday"] == date(2027, 3, 29)
    assert days["Independence Day"] == date(2027, 9, 21) and days["Garifuna Settlement Day"] == date(2027, 11, 19)


def test_holidays_are_added_once(studio):
    first = ensure_holidays(studio["org"], [2027])
    assert first == len(belize_holidays(2027)) and ensure_holidays(studio["org"], [2027]) == 0


# ── Views and scope ──────────────────────────────────────────────────────────


def test_staff_see_the_plan_and_add_to_it(client, studio):
    ws = studio["restaurant"]
    client.force_login(studio["editor"])
    url = reverse("planning:board", kwargs={"workspace_id": ws.id})
    assert client.get(url).status_code == 200
    response = client.post(
        reverse("planning:quick_add", kwargs={"workspace_id": ws.id}),
        {"title": "Horchata", "target_date": (_today() + timedelta(4)).isoformat(), "format": "photo"},
    )
    assert response.status_code == 302
    plan = ContentPlan.objects.get(post__title="Horchata")
    assert plan.workspace == ws and plan.format == "photo"


def test_viewers_cannot_plan(client, studio):
    ws = studio["restaurant"]
    client.force_login(studio["viewer"])
    assert client.get(reverse("planning:board", kwargs={"workspace_id": ws.id})).status_code == 200
    response = client.post(
        reverse("planning:quick_add", kwargs={"workspace_id": ws.id}),
        {"title": "Nope", "target_date": _today().isoformat()},
    )
    assert response.status_code == 403 and not ContentPlan.objects.filter(post__title="Nope").exists()


@pytest.mark.parametrize("name", ["board", "campaigns", "key_dates"])
def test_client_users_never_reach_the_plan(client, studio, name):
    client.force_login(studio["restaurant_client"])
    response = client.get(reverse(f"planning:{name}", kwargs={"workspace_id": studio["restaurant"].id}))
    assert response.status_code in (302, 403, 404)


def test_cross_client_plan_access_is_refused(client, studio):
    """An editor on Casa Maya only cannot open Luma's plan, or a Luma post through Casa Maya's URL."""
    luma_plan = quick_plan(studio["skincare"], studio["owner"], title="Serum", target_date=_today() + timedelta(3))
    client.force_login(studio["editor"])
    assert client.get(reverse("planning:board", kwargs={"workspace_id": studio["skincare"].id})).status_code in (
        302,
        403,
        404,
    )
    sneaky = reverse("planning:post", kwargs={"workspace_id": studio["restaurant"].id, "post_id": luma_plan.post.id})
    assert client.get(sneaky).status_code == 404
    stage_url = reverse(
        "planning:stage", kwargs={"workspace_id": studio["restaurant"].id, "post_id": luma_plan.post.id}
    )
    assert client.post(stage_url, {"stage": "in_production"}).status_code == 404
    luma_plan.refresh_from_db()
    assert luma_plan.stage == ContentPlan.Stage.PLANNED


def test_all_clients_plan_lists_only_the_clients_you_work_on(client, studio):
    quick_plan(studio["skincare"], studio["owner"], title="Luma only", target_date=_today() + timedelta(2))
    quick_plan(studio["restaurant"], studio["owner"], title="Casa only", target_date=_today() + timedelta(2))
    client.force_login(studio["editor"])
    body = client.get(reverse("planning:all")).content.decode()
    assert "Casa only" in body and "Luma only" not in body


def test_calendar_chips_and_list_show_stage_with_status(client, studio):
    ws = studio["restaurant"]
    post = make_post(ws, studio["account"], status="draft", when=timezone.now() + timedelta(days=2), title="Chip")
    ContentPlan.objects.create(post=post, workspace=ws, stage=ContentPlan.Stage.SHOT_NEEDED)
    client.force_login(studio["editor"])
    url = reverse("calendar:calendar", kwargs={"workspace_id": ws.id})
    month = client.get(url + "?mode=calendar&view=month").content.decode()
    assert "Shot needed · Draft" in month
    listed = client.get(url + "?mode=calendar&view=list", HTTP_HX_REQUEST="true").content.decode()
    assert "Shot needed · Draft" in listed


def test_freelancers_do_not_see_the_plan(client, studio):
    """The brief limits freelancers to their own tasks, shoots and files."""
    ws = studio["restaurant"]
    plan = quick_plan(ws, studio["owner"], title="Not for freelancers", target_date=_today() + timedelta(3))
    client.force_login(studio["contributor"])
    for url in (
        reverse("planning:board", kwargs={"workspace_id": ws.id}),
        reverse("planning:post", kwargs={"workspace_id": ws.id, "post_id": plan.post.id}),
        reverse("reviews:post", kwargs={"workspace_id": ws.id, "post_id": plan.post.id}),
    ):
        assert client.get(url).status_code == 403, url
    assert "Not for freelancers" not in client.get(reverse("planning:all")).content.decode()
    response = client.post(
        reverse("planning:quick_add", kwargs={"workspace_id": ws.id}),
        {"title": "Sneaky", "target_date": _today().isoformat()},
    )
    assert response.status_code == 403


def test_plan_button_only_for_people_who_can_open_the_plan(client, studio):
    ws = studio["restaurant"]
    plan_url = reverse("planning:board", kwargs={"workspace_id": ws.id})
    calendar = reverse("calendar:calendar", kwargs={"workspace_id": ws.id})
    client.force_login(studio["editor"])
    assert plan_url in client.get(calendar).content.decode()
    client.force_login(studio["contributor"])
    assert plan_url not in client.get(calendar).content.decode()
