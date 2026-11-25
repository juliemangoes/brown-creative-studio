"""Research: every recommendation shows its evidence or is labelled a hypothesis.

Also: avoided topics are flagged, AI never schedules or approves, recommendations become ideas
or planned drafts, outcomes are judged against the client's usual, and no other client's
research is reachable.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.analytics.models import PostInsightsSnapshot
from apps.audit.models import AuditEvent
from apps.composer.models import Idea, PlatformPost
from apps.research import ai, services, sources
from apps.research.models import ResearchBrief, ResearchItem, SwipeItem
from apps.studio.tests.helpers import make_account, make_post, make_studio


@pytest.fixture
def studio(db):
    people = make_studio()
    people["ig"] = make_account(people["restaurant"], "Casa IG", "instagram")
    people["luma_ig"] = make_account(people["skincare"], "Luma IG", "instagram")
    return people


def _published(ws, account, title, days_ago, engagement, fmt=None):
    from apps.planning.models import ContentPlan

    post = make_post(ws, account, status="published", title=title, when=timezone.now() - timedelta(days=days_ago))
    pp = post.platform_posts.get()
    pp.published_at = timezone.now() - timedelta(days=days_ago)
    pp.save(update_fields=["published_at"])
    PostInsightsSnapshot.objects.create(
        platform_post=pp, metric_key="views", date=timezone.localdate(), value=engagement * 20
    )
    PostInsightsSnapshot.objects.create(
        platform_post=pp, metric_key="likes", date=timezone.localdate(), value=engagement
    )
    if fmt:
        ContentPlan.objects.create(post=post, workspace=ws, format=fmt)
    return post


def _history(studio):
    ws = studio["restaurant"]
    for i in range(5):
        _published(ws, studio["ig"], f"Reel {i}", 10 + i, 500, "reel")
        _published(ws, studio["ig"], f"Photo {i}", 20 + i, 100, "photo")


def _swipe(ws, **kw):
    data = {"title": "Chef plating", "kind": "post", "whose": "competitor", "url": "https://www.instagram.com/p/x/",
            "source_name": "Instagram", "observed_on": timezone.localdate() - timedelta(days=3), "format": "reel"}  # fmt: skip
    data.update(kw)
    return SwipeItem.objects.create(workspace=ws, **data)


# ── The done-when ────────────────────────────────────────────────────────────


def test_every_recommendation_shows_its_evidence_or_is_a_hypothesis(studio, settings):
    settings.RESEARCH_PROVIDER = "mock"
    _history(studio)
    ws = studio["restaurant"]
    _swipe(ws)
    _swipe(ws, title="Market walk", hook="Come with us at 6am")
    _swipe(ws, kind="trend", whose="platform", title="Honest reviews", source_name="TikTok Creative Center", format="")
    profile = services.profile_for(ws)
    profile.keywords = "hudut"
    profile.save()
    brief = services.generate_brief(ws, studio["editor"])
    items = list(brief.items.all())
    assert {i.group for i in items} == {"client", "niche", "emerging"}
    for item in items:
        if item.is_hypothesis:
            assert item.get_confidence_display() == "Hypothesis to test"
        else:
            assert item.evidence and item.sample_size is not None
            for e in item.evidence:
                assert e["date"] and e["source"]
        assert item.observed
    # The client's own pattern is a real comparison with counts.
    client = next(i for i in items if i.group == "client" and i.pattern.get("by") == "format")
    assert "Reel" in client.title and client.sample_size == 10 and "5 posts" in client.observed
    # Mock trends are labelled as test data.
    assert any("Test data" in w for i in items for w in i.warnings)


def test_an_item_without_evidence_is_always_a_hypothesis(studio):
    brief = ResearchBrief.objects.create(workspace=studio["restaurant"], week_start=services.week_start())
    item = ResearchItem.objects.create(brief=brief, workspace=studio["restaurant"], group="emerging", title="Try X",
                                       observed="An idea.", confidence=ResearchItem.Confidence.HIGH)  # fmt: skip
    assert item.confidence == "hypothesis" and item.is_hypothesis


def test_with_no_data_the_brief_says_what_it_could_not_look_at(studio):
    profile = services.profile_for(studio["restaurant"])
    profile.keywords = "brunch"
    profile.save()
    brief = services.generate_brief(studio["restaurant"])
    assert any("too few to compare" in n for n in brief.notes)
    assert any("Save examples to the swipe file" in n for n in brief.notes)
    items = list(brief.items.all())
    assert len(items) == 1 and items[0].is_hypothesis and "brunch" in items[0].title


# ── AI rules ─────────────────────────────────────────────────────────────────


def test_suggestions_that_mention_an_avoided_topic_are_flagged(studio):
    ws = studio["restaurant"]
    profile = services.profile_for(ws)
    profile.topics_to_avoid = "alcohol promotions\npolitics"
    profile.save()
    _swipe(ws, hook="Two-for-one alcohol promotions every Friday")
    _swipe(ws, title="Another reel")
    brief = services.generate_brief(ws)
    flagged = [i for i in brief.items.all() if any("alcohol promotions" in w for w in i.warnings)]
    assert flagged


def test_numbers_the_ai_adds_are_flagged(studio):
    context = ai.client_context(studio["restaurant"])
    facts = {"observed": "5 reels averaged 500", "evidence": [], "sample_size": 10}
    assert ai.checks(["Reels get 3,000 more views"], facts, context)
    assert ai.checks(["5 reels averaged 500"], facts, context) == []


def test_the_claude_writer_gets_only_evidence_and_profile(studio, settings):
    settings.AI_PROVIDER = "claude"
    ws = studio["restaurant"]
    _swipe(ws)
    _swipe(ws, title="Second")
    from apps.brand.services import profile_for as brand_for

    brand = brand_for(ws)
    brand.internal_notes = "Owner is difficult"
    brand.save()
    response = MagicMock(stop_reason="end_turn", model="claude-opus-5-5")
    response.content = [MagicMock(type="text", text='{"why_fits": "Fits the audience.", "hook": "Watch this"}')]
    with patch("anthropic.Anthropic") as anthropic:
        anthropic.return_value.beta.messages.create.return_value = response
        brief = services.generate_brief(ws)
        sent = [c.kwargs for c in anthropic.return_value.beta.messages.create.call_args_list]
    assert sent and all("never invent" in k["system"].lower() for k in sent)
    assert all("Owner is difficult" not in k["messages"][0]["content"] for k in sent)
    assert brief.ai_provider.startswith("claude")
    assert all(i.why_fits == "Fits the audience." for i in brief.items.all())


def test_ai_drafting_switched_off_still_builds_the_brief(studio, settings):
    settings.AI_PROVIDER = "none"
    _swipe(studio["restaurant"])
    _swipe(studio["restaurant"], title="Two")
    brief = services.generate_brief(studio["restaurant"])
    assert brief.items.exists() and not brief.items.filter(ai_drafted=True).exists()


# ── Turning recommendations into content ─────────────────────────────────────


def _item(studio):
    ws = studio["restaurant"]
    _swipe(ws, hook="Watch this plate")
    _swipe(ws, title="Two")
    brief = services.generate_brief(ws)
    return brief.items.filter(group="niche").first()


def test_a_recommendation_becomes_an_idea_once(studio):
    item = _item(studio)
    idea = services.to_idea(item, studio["editor"])
    assert isinstance(idea, Idea) and "Evidence:" in idea.description and item.status == "used"
    with pytest.raises(services.ResearchError):
        services.to_idea(item, studio["editor"])
    assert AuditEvent.objects.filter(verb="research.used").exists()


def test_a_planned_post_is_a_draft_that_still_needs_review(studio):
    item = _item(studio)
    plan = services.to_plan(item, studio["editor"], timezone.localdate() + timedelta(days=5), draft_caption=True)
    assert plan.format == "reel" and plan.hook
    assert plan.post.caption  # drafted for review
    statuses = set(PlatformPost.objects.filter(post=plan.post).values_list("status", flat=True))
    assert statuses == {"draft"}  # AI never schedules, approves or publishes
    item.refresh_from_db()
    assert item.post_id == plan.post_id


# ── Learning from results ────────────────────────────────────────────────────


def test_outcomes_compare_with_the_clients_usual(studio):
    ws = studio["restaurant"]
    for i in range(6):
        _published(ws, studio["ig"], f"Earlier {i}", 20 + i, 100)
    made = _published(ws, studio["ig"], "From the brief", 9, 300)
    brief = ResearchBrief.objects.create(workspace=ws, week_start=services.week_start())
    item = ResearchItem.objects.create(brief=brief, workspace=ws, group="niche", title="Try it", observed="Seen.",
                                       evidence=[{"label": "x", "url": "", "date": "2026-11-01", "source": "s"}],
                                       sample_size=2, confidence="low", status="used", post=made)  # fmt: skip
    assert services.check_outcome(item) == "above"
    assert item.outcome_detail["usual"] == 100 and item.outcome_detail["compared_with"] == 6
    track = {r["group"]: r for r in services.track_record(ws)}
    assert track["Working in this niche"]["above"] == 1


def test_outcomes_wait_a_week(studio):
    ws = studio["restaurant"]
    made = _published(ws, studio["ig"], "Fresh", 2, 300)
    brief = ResearchBrief.objects.create(workspace=ws, week_start=services.week_start())
    item = ResearchItem.objects.create(brief=brief, workspace=ws, group="client", title="t", observed="o",
                                       status="used", post=made)  # fmt: skip
    assert services.check_outcome(item) == "" and item.outcome_checked_at is None


def test_the_weekly_run_makes_one_brief_per_active_client(studio, settings):
    from apps.clients.models import ClientProfile
    from apps.clients.services import profile_for
    from apps.studio.decorators import zone

    settings.RESEARCH_BRIEF_WEEKDAY = timezone.localtime(timezone.now(), zone(None)).weekday()
    for ws in (studio["restaurant"], studio["skincare"]):
        p = profile_for(ws)
        p.stage = ClientProfile.Stage.ACTIVE
        p.save()
    assert services.run_weekly() == 2
    assert services.run_weekly() == 0


def test_sources_are_none_by_default(settings):
    assert isinstance(sources.source(), sources.NoSource)
    settings.RESEARCH_PROVIDER = "mock"
    assert all(
        t.is_test_data and t.source == "Test data"
        for t in sources.source().fetch(MagicMock(**{"lines.return_value": []}, industry=""), timezone.localdate())
    )


# ── Pages, permissions and scope ─────────────────────────────────────────────


def test_pages_render_and_actions_work(studio, client):
    ws = studio["restaurant"].id
    client.force_login(studio["editor"])
    assert client.get(reverse("research:index", kwargs={"workspace_id": ws})).status_code == 200
    client.post(reverse("research:swipe", kwargs={"workspace_id": ws}),
                {"title": "Saved reel", "kind": "post", "whose": "competitor", "observed_on": timezone.localdate().isoformat(),
                 "format": "reel"})  # fmt: skip
    client.post(reverse("research:swipe", kwargs={"workspace_id": ws}),
                {"title": "Saved reel 2", "kind": "post", "whose": "competitor", "observed_on": timezone.localdate().isoformat(),
                 "format": "reel"})  # fmt: skip
    assert SwipeItem.objects.filter(workspace=studio["restaurant"]).count() == 2
    client.post(reverse("research:generate", kwargs={"workspace_id": ws}))
    brief = ResearchBrief.objects.get(workspace=studio["restaurant"])
    item = brief.items.filter(status="new").first()
    resp = client.post(reverse("research:item_action", kwargs={"workspace_id": ws, "item_id": item.id, "action": "plan"}),
                       {"target_date": (timezone.localdate() + timedelta(days=3)).isoformat()})  # fmt: skip
    assert resp.status_code == 302
    item.refresh_from_db()
    assert item.post_id
    for url in (
        reverse("research:index", kwargs={"workspace_id": ws}),
        reverse("research:brief", kwargs={"workspace_id": ws, "brief_id": brief.id}),
        reverse("research:swipe", kwargs={"workspace_id": ws}),
        reverse("research:profile", kwargs={"workspace_id": ws}),
        reverse("research:all"),
    ):
        assert client.get(url).status_code == 200, url


def test_viewers_can_read_but_not_change(studio, client):
    ws = studio["restaurant"].id
    client.force_login(studio["viewer"])
    assert client.get(reverse("research:index", kwargs={"workspace_id": ws})).status_code == 200
    assert client.post(reverse("research:generate", kwargs={"workspace_id": ws})).status_code == 403
    assert client.post(reverse("research:profile", kwargs={"workspace_id": ws}), {"industry": "x"}).status_code == 403


def test_another_clients_research_is_not_reachable_by_changing_the_url(studio, client):
    _swipe(studio["skincare"])
    _swipe(studio["skincare"], title="Two")
    luma_brief = services.generate_brief(studio["skincare"])
    luma_item = luma_brief.items.first()
    luma_swipe = SwipeItem.objects.filter(workspace=studio["skincare"]).first()
    restaurant = studio["restaurant"].id
    client.force_login(studio["manager"])  # works on the restaurant only
    assert (
        client.get(
            reverse("research:brief", kwargs={"workspace_id": restaurant, "brief_id": luma_brief.id})
        ).status_code
        == 404
    )
    assert client.post(reverse("research:item_action", kwargs={"workspace_id": restaurant, "item_id": luma_item.id,
                                                              "action": "idea"})).status_code == 404  # fmt: skip
    assert (
        client.post(
            reverse("research:swipe_delete", kwargs={"workspace_id": restaurant, "item_id": luma_swipe.id})
        ).status_code
        == 404
    )
    assert client.get(reverse("research:index", kwargs={"workspace_id": studio["skincare"].id})).status_code in (
        403,
        404,
    )
    assert "Luma" not in client.get(reverse("research:all")).content.decode()
    luma_item.refresh_from_db()
    assert luma_item.status == "new" and SwipeItem.objects.filter(pk=luma_swipe.pk).exists()


def test_clients_never_reach_research(studio, client):
    client.force_login(studio["restaurant_client"])
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(studio["restaurant"].id)
    session.save()
    assert client.get(reverse("research:index", kwargs={"workspace_id": studio["restaurant"].id})).status_code in (
        302,
        403,
        404,
    )
