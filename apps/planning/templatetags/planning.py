from django import template

from apps.planning.models import ContentPlan
from apps.planning.services import STAGE_HIDDEN_STATUSES, combined_label

register = template.Library()


@register.simple_tag(takes_context=True)
def production_label(context, platform_post):
    """Production stage and editorial status as one label, e.g. "Shot needed · Draft" (cached per render)."""
    cache = context.render_context.setdefault("planning_stages", {})
    post_id = platform_post.post_id
    if post_id not in cache:
        cache[post_id] = ContentPlan.objects.filter(post_id=post_id).only("stage").first()
    return combined_label(platform_post, plan=cache[post_id] or None)


@register.simple_tag(takes_context=True)
def production_stage(context, post, status=""):
    """The production stage of a post still in the works ("Shot needed"), or "" (cached per render)."""
    status = status or getattr(post, "status", "")
    if status in STAGE_HIDDEN_STATUSES:
        return ""
    cache = context.render_context.setdefault("planning_stages", {})
    if post.pk not in cache:
        cache[post.pk] = ContentPlan.objects.filter(post_id=post.pk).only("stage").first()
    plan = cache[post.pk]
    return plan.get_stage_display() if plan else ""
