"""Show only the studio's platforms on the connect page: Facebook, Instagram and TikTok.

Uses BrightBean's PlatformVisibility rows, so an admin can change it later in the
admin site (Connect page platforms). Instagram is the Facebook-login variant, which
uses the same Meta app and review as Facebook.
"""

from django.db import migrations

STUDIO_PLATFORMS = {"facebook", "instagram", "tiktok"}


def hide_others(apps, schema_editor):
    PlatformVisibility = apps.get_model("social_accounts", "PlatformVisibility")
    from apps.credentials.models import PlatformCredential

    for value, _label in PlatformCredential.Platform.choices:
        PlatformVisibility.objects.update_or_create(platform=value, defaults={"is_visible": value in STUDIO_PLATFORMS})


def show_all(apps, schema_editor):
    apps.get_model("social_accounts", "PlatformVisibility").objects.update(is_visible=True)


class Migration(migrations.Migration):
    dependencies = [
        ("studio", "0002_preference"),
        ("social_accounts", "0025_youtuberevocation_and_more"),
    ]

    operations = [migrations.RunPython(hide_others, show_all)]
