"""A client's brand: profile, project stages with client sign-off, and brand files.

The profile is the one place the brand lives. Onboarding fills it, the studio
edits it, the portal's Brand Hub shows it, and content planning and AI drafting
read it (``BrandProfile.as_brief``).
"""

import uuid

from django.conf import settings
from django.db import models

from apps.common.managers import WorkspaceScopedModel
from apps.projects.defaults import BRAND_STAGES
from apps.studio.files import PrivateFile


class BrandProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.OneToOneField("workspaces.Workspace", on_delete=models.CASCADE, related_name="brand_profile")

    # Strategy
    purpose = models.TextField(blank=True, default="")
    positioning = models.TextField(blank=True, default="")
    values = models.JSONField(default=list, blank=True)  # [str]
    personality = models.JSONField(default=list, blank=True)  # [str]
    audiences = models.JSONField(default=list, blank=True)  # [{"name", "description"}]
    competitors = models.JSONField(default=list, blank=True)  # [str]

    # Verbal identity
    voice = models.TextField(blank=True, default="")
    voice_do = models.JSONField(default=list, blank=True)
    voice_dont = models.JSONField(default=list, blank=True)
    tagline = models.CharField(max_length=200, blank=True, default="")
    key_messages = models.JSONField(default=list, blank=True)
    elevator_pitch = models.TextField(blank=True, default="")

    # Visual identity
    colors = models.JSONField(default=list, blank=True)  # [{"name", "hex", "role"}]
    fonts = models.JSONField(default=list, blank=True)  # [{"name", "usage"}]

    # Content inputs (from onboarding): what to show and what to avoid
    content_likes = models.TextField(blank=True, default="")
    content_avoid = models.TextField(blank=True, default="")

    internal_notes = models.TextField(blank=True, default="")
    is_published = models.BooleanField(default=False, help_text="Shown in the client's Brand Hub.")
    published_at = models.DateTimeField(null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brand_profile"

    def __str__(self):
        return f"Brand profile for {self.workspace}"

    @property
    def has_strategy(self) -> bool:
        return any([self.purpose, self.positioning, self.values, self.personality, self.audiences])

    @property
    def has_voice(self) -> bool:
        return any([self.voice, self.voice_do, self.voice_dont, self.tagline, self.key_messages, self.elevator_pitch])

    def as_brief(self) -> str:
        """The brand in plain text, for content planning and AI drafting. Never includes internal notes."""
        lines = [f"Brand: {self.workspace.name}"]

        def add(label, value):
            if isinstance(value, list):
                value = "; ".join(
                    (
                        f"{v.get('name', '')}: {v.get('description', v.get('usage', v.get('hex', '')))}"
                        if isinstance(v, dict)
                        else str(v)
                    )
                    for v in value
                )
            if value:
                lines.append(f"{label}: {value}")

        add("Purpose", self.purpose)
        add("Positioning", self.positioning)
        add("Values", self.values)
        add("Personality", self.personality)
        add("Audiences", self.audiences)
        add("Voice", self.voice)
        add("Do", self.voice_do)
        add("Don't", self.voice_dont)
        add("Tagline", self.tagline)
        add("Key messages", self.key_messages)
        add("Content they like", self.content_likes)
        add("Content to avoid", self.content_avoid)
        add("Competitors", self.competitors)
        return "\n".join(lines)


STAGE_CHOICES = [(key, title) for key, title, _ in BRAND_STAGES]


class BrandStage(WorkspaceScopedModel):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        AWAITING_SIGNOFF = "awaiting_signoff", "Waiting for sign-off"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        SIGNED_OFF = "signed_off", "Signed off"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=20, choices=STAGE_CHOICES)
    position = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    # Shown to the client with the sign-off request.
    client_summary = models.TextField(blank=True, default="")
    internal_notes = models.TextField(blank=True, default="")
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="brand_stages"
    )
    milestone = models.ForeignKey("projects.Task", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # Identity design: the contract's revision limit, and rounds used so far.
    revision_limit = models.PositiveSmallIntegerField(null=True, blank=True)
    revisions_used = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    signed_off_at = models.DateTimeField(null=True, blank=True)
    signed_off_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    client_comment = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brand_stage"
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["workspace", "key"], name="brand_stage_unique_per_client")]

    def __str__(self):
        return self.get_key_display()

    @property
    def over_revision_limit(self) -> bool:
        return self.revision_limit is not None and self.revisions_used > self.revision_limit


class BrandAsset(WorkspaceScopedModel, PrivateFile):
    class Kind(models.TextChoices):
        LOGO = "logo", "Logo"
        FONT = "font", "Font"
        TEMPLATE = "template", "Template"
        EXAMPLE = "example", "Example application"
        GUIDELINE = "guideline", "Guidelines"
        PHOTO = "photo", "Photo"
        REFERENCE = "reference", "Past material or reference"
        OTHER = "other", "Other"

    class Source(models.TextChoices):
        STUDIO = "studio", "Studio"
        CLIENT = "client", "Client"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    title = models.CharField(max_length=160)
    variant = models.CharField(max_length=80, blank=True, default="", help_text="e.g. Primary, Mark, One colour, White")
    stage = models.ForeignKey(BrandStage, on_delete=models.SET_NULL, null=True, blank=True, related_name="assets")
    client_visible = models.BooleanField(default=False, help_text="Shown and downloadable in the client's Brand Hub.")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.STUDIO)
    notes = models.TextField(blank=True, default="")

    # Formats a brand file may be.
    ALLOWED_KINDS = ("image", "svg", "pdf", "postscript", "font", "archive")

    class Meta:
        db_table = "brand_asset"
        ordering = ["kind", "title", "variant", "file_format"]

    def __str__(self):
        return f"{self.title} ({self.file_format})"

    def client_can_see(self) -> bool:
        # Client uploads are theirs; studio files only once shared.
        return self.client_visible or self.source == self.Source.CLIENT

    @property
    def previewable(self) -> bool:
        return self.file_kind in ("image", "svg")
