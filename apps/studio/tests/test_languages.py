"""English and Spanish, chosen per person: the portal, emails, and the switcher."""

import pytest
from django.core import mail
from django.urls import reverse

from apps.billing import services as billing
from apps.clients.models import ClientProfile
from apps.members.models import WorkspaceMembership
from apps.studio.languages import set_language, user_language
from apps.studio.permissions import ensure_client_roles
from apps.studio.tests.helpers import make_studio


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


def test_portal_follows_the_persons_choice(client, studio):
    ws = studio["restaurant"]
    _portal(client, studio["restaurant_client"], ws)
    english = client.get(reverse("client_portal:dashboard"))
    assert "Welcome back" in english.content.decode() and english["Content-Language"] == "en"
    client.post(reverse("studio:set_language"), {"language": "es", "next": reverse("client_portal:dashboard")})
    assert user_language(studio["restaurant_client"]) == "es"
    spanish = client.get(reverse("client_portal:dashboard")).content.decode()
    assert "Bienvenido de nuevo" in spanish and '<html lang="es">' in spanish
    assert "Aprobaciones" in spanish and "Cerrar sesión" in spanish


def test_the_studios_portal_language_applies_until_the_client_chooses(client, studio):
    ws = studio["restaurant"]
    from apps.clients.services import profile_for

    profile_for(ws)  # make sure the profile exists
    ClientProfile.objects.filter(workspace=ws).update(portal_language="es")
    _portal(client, studio["restaurant_client"], ws)
    assert "Bienvenido de nuevo" in client.get(reverse("client_portal:dashboard")).content.decode()
    set_language(studio["restaurant_client"], "en")
    assert "Welcome back" in client.get(reverse("client_portal:dashboard")).content.decode()


def test_billing_pages_and_emails_in_spanish(client, studio, django_capture_on_commit_callbacks):
    ws = studio["restaurant"]
    set_language(studio["restaurant_client"], "es")
    inv = billing.create_invoice(ws, studio["owner"], lines=[{"description": "Gestión de redes", "unit_price": 100}])
    with django_capture_on_commit_callbacks(execute=True):
        inv = billing.send_invoice(inv, studio["owner"])
    assert any("factura" in m.subject and m.to == [studio["restaurant_client"].email] for m in mail.outbox)
    body = next(m.body for m in mail.outbox if m.to == [studio["restaurant_client"].email])
    assert "Hola" in body and "emitió" in body
    _portal(client, studio["restaurant_client"], ws)
    page = client.get(reverse("billing_portal:invoice", kwargs={"invoice_id": inv.id})).content.decode()
    assert "Saldo pendiente" in page and "Cómo pagar" in page and "Vista" in page  # opening it marks it viewed


def test_the_switcher_only_accepts_known_languages_and_safe_redirects(client, studio):
    client.force_login(studio["owner"])
    response = client.post(reverse("studio:set_language"), {"language": "fr", "next": "https://evil.example/"})
    assert response["Location"] == "/" and user_language(studio["owner"]) == ""
    client.post(reverse("studio:set_language"), {"language": "es", "next": "/today/"})
    assert user_language(studio["owner"]) == "es"


def test_signed_out_pages_use_the_browser_language(client, db):
    response = client.get("/accounts/login/", HTTP_ACCEPT_LANGUAGE="es-GT,es;q=0.9")
    assert response["Content-Language"] == "es"
