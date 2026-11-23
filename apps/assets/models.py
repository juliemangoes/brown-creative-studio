"""Usage rights for media library assets, in a model linked one-to-one to BrightBean's ``MediaAsset``."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class AssetRights(models.Model):
    class Source(models.TextChoices):
        STUDIO = "studio", "Studio shoot"
        CLIENT = "client", "Client supplied"
        STOCK = "stock", "Stock licence"
        UGC = "ugc", "Customer content (with permission)"
        LICENSED = "licensed", "Licensed from a creator"

    class Usage(models.TextChoices):
        ANY = "any", "Organic and paid"
        ORGANIC = "organic", "Organic only, no ads"
        INTERNAL = "internal", "Internal only, don't publish"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media_asset = models.OneToOneField("media_library.MediaAsset", on_delete=models.CASCADE, related_name="rights")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.STUDIO)
    usage = models.CharField(max_length=10, choices=Usage.choices, default=Usage.ANY)
    licence = models.TextField(
        blank=True, default="", help_text="Licence terms, credit line, or where permission was given."
    )
    talent_release = models.BooleanField(default=False, help_text="Everyone recognisable has signed a release.")
    release_reference = models.CharField(
        max_length=200, blank=True, default="", help_text="Where the signed releases are kept."
    )
    expires_on = models.DateField(null=True, blank=True, db_index=True)
    shoot = models.ForeignKey(
        "shoots.Shoot", on_delete=models.SET_NULL, null=True, blank=True, related_name="asset_rights"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assets_rights"

    @property
    def expired(self) -> bool:
        return bool(self.expires_on and self.expires_on < timezone.localdate())

    def expires_before(self, day) -> bool:
        return bool(self.expires_on and self.expires_on < day)
