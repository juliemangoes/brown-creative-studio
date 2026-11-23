"""A post sent for review is ready for review: its production stage follows without anyone updating it."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.reviews.models import ApprovalRecord


@receiver(post_save, sender=ApprovalRecord, dispatch_uid="planning_stage_on_submit")
def stage_follows_submission(sender, instance, created, **kwargs):
    if not created or instance.decision not in (
        ApprovalRecord.Decision.SUBMITTED,
        ApprovalRecord.Decision.RESUBMITTED,
    ):
        return
    from apps.planning.models import ContentPlan
    from apps.planning.services import BEFORE_REVIEW, set_stage

    plan = ContentPlan.objects.filter(post_id=instance.post_id).first()
    if plan is not None and plan.stage in BEFORE_REVIEW:
        set_stage(plan, ContentPlan.Stage.READY_FOR_REVIEW, instance.actor, note="sent for review")
