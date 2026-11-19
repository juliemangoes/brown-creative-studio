"""The production static build: what the Docker image runs at build time.

Tests use plain static storage, so a vendored file that points at a missing
asset (a source map, a font) would only fail when the image is built. This runs
collectstatic with the production storage into a temporary directory.
"""

import copy

from django.conf import settings
from django.core.management import call_command
from django.test import override_settings


def test_collectstatic_with_the_production_manifest_storage(tmp_path):
    storages = copy.deepcopy(settings.STORAGES)
    storages["staticfiles"] = {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}
    with override_settings(STATIC_ROOT=tmp_path, STORAGES=storages):
        call_command("collectstatic", interactive=False, verbosity=0)
    assert (tmp_path / "staticfiles.json").exists()
