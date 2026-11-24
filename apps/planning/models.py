"""Content planning on top of BrightBean's composer: production details, campaigns and key dates.

A post's editorial and publishing status stays on BrightBean's ``PlatformPost``.
Its production stage (Idea, Planned, Shot needed, In production, Ready for
review) lives here, with the planning details the composer doesn't hold.
Pillars are BrightBean's ``ContentCategory`` (``Post.category``).
"""

import uuid

from django.conf import settings
from django.db import models

from apps.common.managers import WorkspaceScopedModel
from apps.common.validators import validate_hex_color


class Campaign(WorkspaceScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    objective = models.TextField(blank=True, default="")
    starts_on = models.DateField()
    ends_on = models.DateField()
    color = models.CharField(max_length=7, default="#8A6A4F", validators=[validate_hex_color])
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "planning_campaign"
        ordering = ["starts_on"]

    def __str__(self):
        return self.name


class KeyDate(models.Model):
    """A date worth planning around: a public holiday (studio-wide) or a client's launch or event."""

    class Kind(models.TextChoices):
        HOLIDAY = "holiday", "Holiday"
        LAUNCH = "launch", "Launch"
        EVENT = "event", "Event"
        CLIENT = "client", "Client date"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="key_dates")
    # Empty for a studio-wide date (public holidays); set for one client's dates.
    workspace = models.ForeignKey(
        "workspaces.Workspace", on_delete=models.CASCADE, null=True, blank=True, related_name="key_dates"
    )
    date = models.DateField(db_index=True)
    title = models.CharField(max_length=160)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.CLIENT)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "planning_key_date"
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace", "date", "title"], name="planning_key_date_unique"
            )
        ]

    def __str__(self):
        return f"{self.date}: {self.title}"


class ContentPlan(models.Model):
    class Stage(models.TextChoices):
        IDEA = "idea", "Idea"
        PLANNED = "planned", "Planned"
        SHOT_NEEDED = "shot_needed", "Shot needed"
        IN_PRODUCTION = "in_production", "In production"
        READY_FOR_REVIEW = "ready_for_review", "Ready for review"

    class Format(models.TextChoices):
        PHOTO = "photo", "Photo"
        CAROUSEL = "carousel", "Carousel"
        REEL = "reel", "Reel"
        STORY = "story", "Story"
        VIDEO = "video", "Video"
        TEXT = "text", "Text"
        LIVE = "live", "Live"

    class Objective(models.TextChoices):
        AWARENESS = "awareness", "Awareness"
        ENGAGEMENT = "engagement", "Engagement"
        TRAFFIC = "traffic", "Traffic"
        LEADS = "leads", "Leads"
        SALES = "sales", "Sales"
        COMMUNITY = "community", "Community"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.OneToOneField("composer.Post", on_delete=models.CASCADE, related_name="content_plan")
    workspace = models.ForeignKey("workspaces.Workspace", on_delete=models.CASCADE, related_name="content_plans")
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.PLANNED, db_index=True)
    format = models.CharField(max_length=10, choices=Format.choices, blank=True, default="")
    objective = models.CharField(max_length=12, choices=Objective.choices, blank=True, default="")
    hook = models.CharField(max_length=300, blank=True, default="")
    script = models.TextField(blank=True, default="")
    call_to_action = models.CharField(max_length=200, blank=True, default="")
    hashtags = models.TextField(blank=True, default="")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="planned_posts"
    )
    campaign = models.ForeignKey(Campaign, on_delete=models.SET_NULL, null=True, blank=True, related_name="plans")
    target_date = models.DateField(null=True, blank=True, db_index=True)
    draft_due = models.DateField(null=True, blank=True)
    review_due = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "planning_content_plan"

    def __str__(self):
        return f"Plan for {self.post_id}"

    def save(self, *args, **kwargs):
        self.workspace_id = self.post.workspace_id
        super().save(*args, **kwargs)
