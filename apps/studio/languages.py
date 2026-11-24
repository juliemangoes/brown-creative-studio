"""Interface language, chosen per person (English or Spanish).

Order: the person's own choice; for a client without one, the language the studio
set for that client's portal; otherwise the browser's language; otherwise English.
"""

from __future__ import annotations

from contextlib import contextmanager

from django.conf import settings
from django.utils import translation

SUPPORTED = {code for code, _ in settings.INTERFACE_LANGUAGES}


def user_language(user, workspace=None) -> str:
    """The language to use for ``user`` (e.g. for an email), or "" when there is no preference."""
    if user is None or not getattr(user, "pk", None):
        return ""
    from apps.studio.models import Preference

    pref = Preference.objects.filter(user=user).values_list("language", flat=True).first()
    if pref in SUPPORTED:
        return pref
    if workspace is not None:
        from apps.clients.models import ClientProfile

        ws_id = getattr(workspace, "pk", workspace)
        portal = ClientProfile.objects.filter(workspace_id=ws_id).values_list("portal_language", flat=True).first()
        if portal in SUPPORTED:
            return portal
    return ""


@contextmanager
def for_user(user, workspace=None):
    """Render emails and messages in the recipient's language."""
    lang = user_language(user, workspace) or settings.LANGUAGE_CODE
    with translation.override(lang):
        yield lang


def set_language(user, language: str) -> None:
    from apps.studio.models import Preference

    if language not in SUPPORTED and language != "":
        raise ValueError("Unsupported language")
    Preference.objects.update_or_create(user=user, defaults={"language": language})
