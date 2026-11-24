"""Backstop: no route retries a post that may already be live without someone checking.

The composer, the calendar, the REST API and MCP can all move a failed post
back to ``scheduled``. When Facebook or Instagram may have published it before
the error, ``apps.publishing.services.retry`` (with "I checked: it is not live")
is the only way back, because it clears the flag before saving.
"""

from django.db.models.signals import pre_save
from django.dispatch import receiver

from apps.composer.models import PlatformPost
from apps.publishing.services import OUTCOME_UNKNOWN_KEY, PublishingError


@receiver(pre_save, sender=PlatformPost)
def _no_blind_retry(sender, instance, **kwargs):
    if instance._state.adding or instance.status not in ("scheduled", "publishing"):
        return
    before = PlatformPost.objects.filter(pk=instance.pk).values("status", "platform_extra").first()
    if not before or before["status"] != "failed":
        return
    if (before["platform_extra"] or {}).get(OUTCOME_UNKNOWN_KEY) and (instance.platform_extra or {}).get(
        OUTCOME_UNKNOWN_KEY
    ):
        raise PublishingError(
            "This post may already be live. Check the page, then retry it from Publishing "
            "(or paste its link if it is there)."
        )
