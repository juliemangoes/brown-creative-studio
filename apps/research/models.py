"""Research for each client: a profile, a swipe file, and a weekly brief of recommendations.

The brief's rule, enforced in ``ResearchItem.save``: a recommendation either
carries evidence (what was observed, a source, a date, a sample size) or it is
labelled a hypothesis to test. Nothing in between.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.common.managers import WorkspaceScopedModel


class ResearchProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.OneToOneField("workspaces.Workspace", on_delete=models.CASCADE, related_name="research_profile")
    industry = models.CharField(max_length=160, blank=True, default="")
    location = models.CharField(max_length=160, blank=True, default="")
    language = models.CharField(max_length=40, blank=True, default="", help_text="Language(s) the audience reads.")
    audience = models.TextField(blank=True, default="")
    competitors = models.TextField(blank=True, default="", help_text="One per line: a name, handle or link.")
    reference_accounts = models.TextField(blank=True, default="", help_text="Accounts to learn from, one per line.")
    keywords = models.TextField(blank=True, default="", help_text="One per line.")
    hashtags = models.TextField(blank=True, default="", help_text="One per line, with or without #.")
    topics_to_avoid = models.TextField(
        blank=True, default="", help_text="One per line. Suggestions that mention these are flagged."
    )
    updated_at = models.DateTimeField(auto_now=True)

    def lines(self, field: str) -> list[str]:
        return [line.strip() for line in (getattr(self, field) or "").splitlines() if line.strip()]

    def __str__(self):
        return f"Research profile: {self.workspace}"


class SwipeItem(WorkspaceScopedModel):
    """Something the team saved: a reference post, a hook, a format, a trend, a sound."""

    class Kind(models.TextChoices):
        POST = "post", "Reference post"
        HOOK = "hook", "Hook"
        FORMAT = "format", "Format"
        TREND = "trend", "Trend or topic"
        HASHTAG = "hashtag", "Hashtag"
        AUDIO = "audio", "Sound or audio"

    class Whose(models.TextChoices):
        COMPETITOR = "competitor", "A competitor"
        REFERENCE = "reference", "A reference account"
        PLATFORM = "platform", "The platform (trend lists, Creative Center)"
        OTHER = "other", "Somewhere else"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.POST)
    whose = models.CharField(max_length=12, choices=Whose.choices, default=Whose.COMPETITOR)
    title = models.CharField(max_length=200)
    url = models.URLField(max_length=1000, blank=True, default="")
    platform = models.CharField(max_length=20, blank=True, default="")
    account = models.CharField(max_length=160, blank=True, default="", help_text="Whose post or list it is.")
    source_name = models.CharField(max_length=160, blank=True, default="", help_text="Where it was found.")
    observed_on = models.DateField(help_text="When it was seen.")
    format = models.CharField(max_length=20, blank=True, default="")
    hook = models.CharField(max_length=300, blank=True, default="")
    metric_note = models.CharField(
        max_length=160, blank=True, default="", help_text="What the source showed, e.g. 1.2M views."
    )
    notes = models.TextField(blank=True, default="")
    expires_on = models.DateField(null=True, blank=True)
    saved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_on", "-created_at"]

    def __str__(self):
        return self.title


class ResearchBrief(WorkspaceScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    week_start = models.DateField()
    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    ai_provider = models.CharField(max_length=40, blank=True, default="")
    sources_used = models.JSONField(default=list, blank=True)
    notes = models.JSONField(default=list, blank=True)  # what could not be looked at, and why

    class Meta:
        ordering = ["-week_start", "-generated_at"]

    def __str__(self):
        return f"Brief for {self.workspace} · week of {self.week_start}"


class ResearchItem(models.Model):
    """One recommendation in a brief."""

    class Group(models.TextChoices):
        CLIENT = "client", "Working for this client"
        NICHE = "niche", "Working in this niche"
        EMERGING = "emerging", "Emerging now"

    class Confidence(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"
        HYPOTHESIS = "hypothesis", "Hypothesis to test"

    class Status(models.TextChoices):
        NEW = "new", "New"
        USED = "used", "Turned into content"
        DISMISSED = "dismissed", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brief = models.ForeignKey(ResearchBrief, on_delete=models.CASCADE, related_name="items")
    workspace = models.ForeignKey("workspaces.Workspace", on_delete=models.CASCADE, related_name="+")
    group = models.CharField(max_length=10, choices=Group.choices)
    title = models.CharField(max_length=200)
    observed = models.TextField(help_text="What was observed, in plain words.")
    # [{"label", "url", "date", "source"}]: where the observation comes from.
    evidence = models.JSONField(default=list, blank=True)
    sample_size = models.PositiveIntegerField(null=True, blank=True)
    confidence = models.CharField(max_length=10, choices=Confidence.choices, default=Confidence.HYPOTHESIS)
    why_fits = models.TextField(blank=True, default="")
    expires_on = models.DateField(null=True, blank=True)
    suggested_format = models.CharField(max_length=40, blank=True, default="")
    suggested_hook = models.CharField(max_length=300, blank=True, default="")
    # The pattern the outcome is judged by: {"metric": "engagement", "format": "reel", ...}
    pattern = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    ai_drafted = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    idea = models.ForeignKey("composer.Idea", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    post = models.ForeignKey("composer.Post", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Learning from results: how the content made from it did against the client's usual.
    outcome = models.CharField(max_length=10, blank=True, default="")  # above | typical | below
    outcome_detail = models.JSONField(default=dict, blank=True)
    outcome_checked_at = models.DateTimeField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["group", "position", "created_at"]

    @property
    def is_hypothesis(self) -> bool:
        return self.confidence == self.Confidence.HYPOTHESIS

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence) and self.sample_size is not None

    def save(self, *args, **kwargs):
        # No evidence, no confidence: it is a hypothesis to test.
        if not self.has_evidence:
            self.confidence = self.Confidence.HYPOTHESIS
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title
