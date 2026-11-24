"""Phase 4's goal: one post goes from idea to shoot to client approval, with a full history."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.approvals import services as approvals
from apps.approvals.models import PostComment
from apps.composer.models import Idea
from apps.planning.models import ContentPlan
from apps.planning.services import plan_idea
from apps.reviews.history import post_history
from apps.reviews.services import clearance
from apps.shoots import services as shoots
from apps.shoots.models import Shoot
from apps.studio.tests.helpers import make_account, make_studio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def studio(db):
    people = make_studio()
    ws = people["restaurant"]
    ws.approval_workflow_mode = "required_internal_and_client"
    ws.save(update_fields=["approval_workflow_mode"])
    people["account"] = make_account(ws, "Casa Maya")
    return people


def test_idea_to_shoot_to_client_approval_with_full_history(client, studio):
    ws, editor, owner, client_user = (
        studio["restaurant"],
        studio["editor"],
        studio["owner"],
        studio["restaurant_client"],
    )
    today = timezone.localdate()

    # 1. Idea → planned post (no media yet, so a shot is needed).
    idea = Idea.objects.create(workspace=ws, author=editor, title="Chef's table", description="Rosa plating the mole")
    plan = plan_idea(idea, editor, target_date=today + timedelta(days=10))
    post = plan.post
    assert plan.stage == ContentPlan.Stage.SHOT_NEEDED

    # 2. On a shoot's list, captured on set (offline sync), material matched by code.
    shoot = Shoot.objects.create(workspace=ws, title="Menu shoot", date=today, call_time="09:00")
    shot = shoots.shots_from_posts(shoot, [post], owner)[0]
    shoots.apply_ops(shoot, editor, [{"op": "status", "shot": str(shot.id), "status": "captured"}])
    matched, unmatched, refused = shoots.upload_and_match(
        shoot, editor, [SimpleUploadedFile(f"{shot.code}_mole.png", PNG, content_type="image/png")]
    )
    assert len(matched) == 1 and not refused
    plan.refresh_from_db()
    assert plan.stage == ContentPlan.Stage.IN_PRODUCTION and post.media_attachments.count() == 1

    # 3. Studio review, then the client's approval (with an internal note the client must never see).
    PostComment.objects.create(post=post, author=editor, body="Team only: crop tighter", visibility="internal")
    approvals.submit_for_review(post, editor, ws)
    plan.refresh_from_db()
    assert plan.stage == ContentPlan.Stage.READY_FOR_REVIEW  # follows the submission
    approvals.approve_post(post, owner, ws)
    approvals.approve_post(post, client_user, ws)
    pp = post.platform_posts.get()
    assert pp.status == "approved" and clearance(pp)["cleared"]

    # The full history, in order, from idea to the client's approval.
    events = post_history(post)
    titles = [e.title for e in events]
    kinds = [e.kind for e in events]
    assert kinds[0] == "idea" and "Chef's table" in titles[0]
    for expected in (
        "Planned from the idea",
        "added to Menu shoot",
        "captured on set",
        "attached (shot",
        "Production stage: Shot needed → In production",
        "Production stage: In production → Ready for review (sent for review)",
        "Studio review: submitted",
        "Studio review: approved",
        "Client approval: approved",
    ):
        assert any(expected in t for t in titles), (expected, "\n".join(titles))
    assert "Client approval: approved" in titles[-1] or "Client approval: approved" in titles[-2]
    assert any(e.kind == "version" for e in events)
    assert any(e.internal for e in events)  # the studio sees the internal note
    assert not any(e.internal for e in post_history(post, include_internal=False))  # the client never does

    # The studio's review page shows it.
    client.force_login(owner)
    page = client.get(reverse("reviews:post", kwargs={"workspace_id": ws.id, "post_id": post.id})).content.decode()
    assert "Full history" in page and "Chef&#x27;s table" in page and "Client approval: approved" in page
