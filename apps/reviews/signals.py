"""Apply the review rules on every route (composer, calendar, queue, REST API, MCP, admin).

Changes are detected on the models themselves, so no route can edit approved
content without reopening it, and nothing reaches ``scheduled`` or
``publishing`` without the approvals the workspace requires.
"""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from apps.approvals.models import ApprovalAction
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.reviews import services

POST_FIELDS = ("title", "caption", "first_comment", "tags")
PP_FIELDS = (
    "platform_specific_title",
    "platform_specific_caption",
    "platform_specific_first_comment",
    "platform_specific_media",
    "platform_extra",
)
COMMITTED = ("scheduled", "publishing")


@receiver(post_save, sender=ApprovalAction)
def _record(sender, instance, created, **kwargs):
    if created:
        services.record_action(instance)


@receiver(pre_save, sender=Post)
def _stash_post(sender, instance, **kwargs):
    if instance.pk and not instance._state.adding:
        instance._reviews_before = Post.objects.filter(pk=instance.pk).values(*POST_FIELDS).first()


@receiver(post_save, sender=Post)
def _post_changed(sender, instance, created, **kwargs):
    before = getattr(instance, "_reviews_before", None)
    if created or not before:
        return
    changed = [f for f in POST_FIELDS if before[f] != getattr(instance, f)]
    if changed:
        services.reopen(instance, f"{', '.join(f.replace('_', ' ') for f in changed)} changed after review")
    instance._reviews_before = None


@receiver(post_save, sender=PostMedia)
@receiver(post_delete, sender=PostMedia)
def _media_changed(sender, instance, **kwargs):
    post = Post.objects.filter(pk=instance.post_id).first()
    if post is not None:
        services.reopen(post, "media changed after review")


@receiver(pre_save, sender=MediaAsset)
def _stash_asset(sender, instance, **kwargs):
    if instance.pk and not instance._state.adding:
        instance._reviews_before = (
            MediaAsset.objects.filter(pk=instance.pk).values("file", "current_version_id").first()
        )


@receiver(post_save, sender=MediaAsset)
def _asset_replaced(sender, instance, created, **kwargs):
    before = getattr(instance, "_reviews_before", None)
    if created or not before:
        return
    if before["file"] != instance.file.name or before["current_version_id"] != instance.current_version_id:
        for post in Post.objects.filter(media_attachments__media_asset=instance).distinct():
            services.reopen(post, f"{instance.filename} was replaced after review")


@receiver(pre_save, sender=PlatformPost)
def _platform_post_guard(sender, instance, **kwargs):
    before = None
    if instance.pk and not instance._state.adding:
        before = PlatformPost.objects.filter(pk=instance.pk).values("status", *PP_FIELDS).first()
    old_status = before["status"] if before else None
    # 1. Nothing is committed to the publisher without the required approvals of this exact content.
    if instance.status in COMMITTED and old_status not in (*COMMITTED, "failed"):
        services.check_schedulable(instance)
    # 2. Per-platform overrides or settings changed: reopen after saving.
    instance._reviews_reopen = bool(
        before
        and before["status"] in services.REOPEN_FROM
        and any(_authored(f, before[f]) != _authored(f, getattr(instance, f)) for f in PP_FIELDS)
    )


def _authored(field, value):
    return services.strip_system_keys(value) if field == "platform_extra" else value


@receiver(post_save, sender=PlatformPost)
def _platform_post_changed(sender, instance, created, **kwargs):
    if getattr(instance, "_reviews_reopen", False):
        instance._reviews_reopen = False
        services.reopen(
            instance.post,
            f"{instance.social_account.get_platform_display()} settings changed after review",
            platform_post=instance,
        )
