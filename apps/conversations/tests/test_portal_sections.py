"""Portal sections added in Phase 5: Messages, Calendar, Shoots and "Coming up" on the home page."""

from datetime import time, timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.billing import services as billing
from apps.conversations.models import Conversation, Message
from apps.members.models import WorkspaceMembership
from apps.notifications.models import Notification
from apps.shoots.models import Shoot
from apps.studio.permissions import ensure_client_roles
from apps.studio.tests.helpers import make_account, make_post, make_studio


@pytest.fixture
def studio(db):
    people = make_studio()
    roles = ensure_client_roles(people["org"])
    WorkspaceMembership.objects.filter(user=people["restaurant_client"]).update(custom_role=roles["Client admin"])
    return people


def _portal(client, user, ws):
    client.force_login(user)
    session = client.session
    session["is_portal_session"] = True
    session["portal_workspace_id"] = str(ws.id)
    session.save()


# ── Messages ─────────────────────────────────────────────────────────────────


def test_client_asks_about_an_invoice_and_the_studio_replies(client, studio, django_capture_on_commit_callbacks):
    ws = studio["restaurant"]
    inv = billing.send_invoice(
        billing.create_invoice(ws, studio["owner"], lines=[{"description": "x", "unit_price": 10}]),
        studio["owner"],
        notify=False,
    )
    _portal(client, studio["restaurant_client"], ws)
    page = client.get(reverse("conversations_portal:index") + f"?about=invoice&id={inv.id}").content.decode()
    assert inv.number in page
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("conversations_portal:index"),
            {"about": "invoice", "about_id": str(inv.id), "subject": "", "body": "Can we pay in two parts?"},
        )
    conversation = Conversation.objects.get(workspace=ws)
    assert conversation.about == "invoice" and conversation.about_label == inv.number
    assert Notification.objects.filter(user=studio["owner"], event_type="client_message").exists()
    assert not Notification.objects.filter(user=studio["editor"], event_type="client_message").exists()  # no billing

    client.force_login(studio["owner"])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("conversations:reply", kwargs={"workspace_id": ws.id, "conversation_id": conversation.id}),
            {"body": "Yes, of course."},
        )
    assert Message.objects.filter(conversation=conversation).count() == 2
    assert any("New message" in m.subject for m in mail.outbox)


def test_billing_threads_stay_with_people_who_see_billing(client, studio):
    ws = studio["restaurant"]
    from apps.conversations import services

    billing_thread = services.start(ws, studio["owner"], subject="Invoice", body="About the deposit", from_client=False)
    Conversation.objects.filter(pk=billing_thread.pk).update(about="invoice")
    general = services.start(ws, studio["owner"], subject="Brunch photos", body="Love them", from_client=False)
    client.force_login(studio["editor"])  # staff without billing
    body = client.get(reverse("conversations:client", kwargs={"workspace_id": ws.id})).content.decode()
    assert "Brunch photos" in body and "About the deposit" not in body
    assert (
        client.get(
            reverse("conversations:thread", kwargs={"workspace_id": ws.id, "conversation_id": billing_thread.id})
        ).status_code
        == 404
    )
    roles = ensure_client_roles(studio["org"])
    WorkspaceMembership.objects.filter(user=studio["restaurant_client"]).update(custom_role=roles["Client reviewer"])
    _portal(client, studio["restaurant_client"], ws)
    assert (
        client.get(reverse("conversations_portal:thread", kwargs={"conversation_id": billing_thread.id})).status_code
        == 404
    )
    assert client.get(reverse("conversations_portal:thread", kwargs={"conversation_id": general.id})).status_code == 200


def test_cross_client_messages_are_refused(client, studio):
    from apps.conversations import services

    luma = services.start(studio["skincare"], studio["owner"], subject="Luma secret", body="hi", from_client=False)
    _portal(client, studio["restaurant_client"], studio["restaurant"])
    assert client.get(reverse("conversations_portal:thread", kwargs={"conversation_id": luma.id})).status_code == 404
    assert (
        client.post(
            reverse("conversations_portal:reply", kwargs={"conversation_id": luma.id}), {"body": "x"}
        ).status_code
        == 404
    )
    assert "Luma secret" not in client.get(reverse("conversations_portal:index")).content.decode()
    # Asking "about" another client's invoice falls back to a general question.
    luma_inv = billing.create_invoice(
        studio["skincare"], studio["owner"], lines=[{"description": "x", "unit_price": 1}]
    )
    luma_inv = billing.send_invoice(luma_inv, studio["owner"], notify=False)
    client.post(reverse("conversations_portal:index"), {"about": "invoice", "about_id": str(luma_inv.id), "body": "hm"})
    mine = Conversation.objects.get(workspace=studio["restaurant"])
    assert mine.about == "general" and mine.about_id is None
    client.force_login(studio["editor"])
    url = reverse("conversations:thread", kwargs={"workspace_id": studio["restaurant"].id, "conversation_id": luma.id})
    assert client.get(url).status_code == 404


# ── Calendar and shoots ──────────────────────────────────────────────────────


def test_portal_calendar_shows_approved_and_scheduled_only(client, studio):
    ws = studio["restaurant"]
    account = make_account(ws, "Casa Maya")
    when = timezone.now() + timedelta(days=1)
    make_post(ws, account, status="scheduled", when=when, title="Marimba night")
    make_post(ws, account, status="draft", when=when, title="Secret draft")
    make_post(ws, account, status="pending_review", when=when, title="Studio still reviewing")
    make_post(
        studio["skincare"], make_account(studio["skincare"], "Luma"), status="scheduled", when=when, title="Luma post"
    )
    _portal(client, studio["restaurant_client"], ws)
    body = client.get(reverse("planning_portal:calendar")).content.decode()
    assert "Marimba night" in body
    assert "Secret draft" not in body and "Studio still reviewing" not in body and "Luma post" not in body


def test_portal_shoots_hide_internal_notes_and_let_clients_tick_prep(client, studio):
    ws = studio["restaurant"]
    shoot = Shoot.objects.create(
        workspace=ws, title="December shoot", date=timezone.localdate() + timedelta(days=3), call_time=time(8),
        notes="INTERNAL: client late paying", prep_checklist=[{"text": "Table laid", "done": False}, {"text": "Ponche", "done": False}],
    )  # fmt: skip
    Shoot.objects.create(workspace=studio["skincare"], title="Luma shoot", date=shoot.date, call_time=time(9))
    _portal(client, studio["restaurant_client"], ws)
    body = client.get(reverse("shoots_portal:index")).content.decode()
    assert "December shoot" in body and "Table laid" in body
    assert "INTERNAL" not in body and "Luma shoot" not in body
    client.post(reverse("shoots_portal:prep", kwargs={"shoot_id": shoot.id}), {"done": ["1"]})
    shoot.refresh_from_db()
    assert [i["done"] for i in shoot.prep_checklist] == [False, True]
    luma = Shoot.objects.get(title="Luma shoot")
    assert client.post(reverse("shoots_portal:prep", kwargs={"shoot_id": luma.id}), {"done": ["0"]}).status_code == 404


def test_home_lists_what_is_coming_up(client, studio):
    ws = studio["restaurant"]
    Shoot.objects.create(
        workspace=ws, title="Brunch shoot", date=timezone.localdate() + timedelta(days=2), call_time=time(8)
    )
    inv = billing.send_invoice(
        billing.create_invoice(ws, studio["owner"], lines=[{"description": "x", "unit_price": 10}]),
        studio["owner"],
        notify=False,
    )
    _portal(client, studio["restaurant_client"], ws)
    body = client.get(reverse("client_portal:dashboard")).content.decode()
    assert "Coming up" in body and "Brunch shoot" in body and inv.number in body
