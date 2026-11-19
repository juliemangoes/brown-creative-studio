"""Private client files: validated, scanned, stored privately, downloaded through short-lived signed links.

Brand files, onboarding uploads and later contracts are client data, so they
never sit at a public URL:

- On S3 they are private objects under ``private/``; a download redirects to a
  presigned URL that expires in a minute.
- On local storage they live in ``PRIVATE_MEDIA_ROOT``, which is never routed;
  the download view streams them.

A link is ``/files/<token>/`` where the token is signed and expires after
``FILE_LINK_TTL`` seconds (default 300). Possessing a link is not enough: the
view checks again that the signed-in user may see that client's file.

Uploads are identified by their bytes, not their name or declared type, then
scanned (``apps.studio.scanning``) before they are stored.
"""

from __future__ import annotations

import contextlib
import os
import uuid

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.studio import scanning

SALT = "studio.private-file"

# kind -> {extension: mime}
FORMATS: dict[str, dict[str, str]] = {
    "image": {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"},
    "svg": {"svg": "image/svg+xml"},
    "pdf": {"pdf": "application/pdf"},
    "postscript": {"eps": "application/postscript", "ai": "application/postscript"},
    "font": {"ttf": "font/ttf", "otf": "font/otf", "woff": "font/woff", "woff2": "font/woff2"},
    "archive": {
        "zip": "application/zip",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
}
INLINE_KINDS = {"image", "pdf"}  # everything else always downloads


def _sniff(head: bytes, ext: str) -> str | None:
    """The format kind the bytes prove, given the claimed extension; None if they don't match."""
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    checks = {
        "png": head.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": head.startswith(b"\xff\xd8\xff"),
        "gif": head.startswith((b"GIF87a", b"GIF89a")),
        "webp": head[:4] == b"RIFF" and head[8:12] == b"WEBP",
        "svg": stripped.startswith((b"<?xml", b"<svg", b"<!--")) and b"<svg" in head,
        "pdf": head.startswith(b"%PDF-"),
        "eps": head.startswith((b"%!PS", b"\xc5\xd0\xd3\xc6")),
        "ai": head.startswith((b"%PDF-", b"%!PS")),
        "ttf": head.startswith((b"\x00\x01\x00\x00", b"true")),
        "otf": head.startswith(b"OTTO"),
        "woff": head.startswith(b"wOFF"),
        "woff2": head.startswith(b"wOF2"),
        "zip": head.startswith(b"PK\x03\x04"),
    }
    checks["jpeg"] = checks["jpg"]
    for zipped in ("docx", "pptx", "xlsx"):
        checks[zipped] = checks["zip"]
    if not checks.get(ext):
        return None
    return next(kind for kind, exts in FORMATS.items() if ext in exts)


def identify(uploaded_file, allowed_kinds) -> tuple[str, str, str]:
    """(kind, extension, mime) for an upload, or ValidationError if its bytes don't match an allowed format."""
    name = getattr(uploaded_file, "name", "") or ""
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    allowed = {e for k in allowed_kinds for e in FORMATS[k]}
    if ext not in allowed:
        raise ValidationError(f"{name or 'This file'} isn't a supported format. Use {', '.join(sorted(allowed))}.")
    try:
        uploaded_file.seek(0)
        head = uploaded_file.read(1024)
    finally:
        with contextlib.suppress(OSError, ValueError):
            uploaded_file.seek(0)
    kind = _sniff(head or b"", ext)
    if kind is None:
        raise ValidationError(f"{name} doesn't look like a real .{ext} file.")
    return kind, ("jpg" if ext == "jpeg" else ext), FORMATS[kind][ext]


def check_upload(uploaded_file, allowed_kinds, *, max_bytes=None) -> tuple[str, str, str, scanning.ScanResult]:
    """Identify, size-check and scan an upload. Raises ValidationError when it must be refused."""
    max_bytes = max_bytes or getattr(settings, "PRIVATE_FILE_MAX_BYTES", 50 * 1024 * 1024)
    if (getattr(uploaded_file, "size", 0) or 0) > max_bytes:
        raise ValidationError(f"{uploaded_file.name} is larger than {max_bytes // (1024 * 1024)} MB.")
    kind, ext, mime = identify(uploaded_file, allowed_kinds)
    result = scanning.scan(uploaded_file)
    if result.status == scanning.INFECTED:
        raise ValidationError(f"{uploaded_file.name} was refused: it contains malware ({result.detail}).")
    if result.status == scanning.ERROR:
        raise ValidationError(f"{uploaded_file.name} couldn't be checked for malware. Please try again shortly.")
    return kind, ext, mime, result


# ── Storage ──────────────────────────────────────────────────────────────────


class PrivateFileSystemStorage(FileSystemStorage):
    """Local private storage: PRIVATE_MEDIA_ROOT, read at use (so tests can point it elsewhere), no URL."""

    @property  # type: ignore[override]
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property  # type: ignore[override]
    def location(self):
        return os.path.abspath(self.base_location)

    @property  # type: ignore[override]
    def base_url(self):
        return None


def private_storage():
    from apps.media_library.storage import is_s3_backend

    if is_s3_backend():
        from storages.backends.s3boto3 import S3Boto3Storage

        return S3Boto3Storage(
            location="private",
            default_acl="private",
            querystring_auth=True,
            file_overwrite=False,
            object_parameters={"CacheControl": "private, no-store"},
        )
    return PrivateFileSystemStorage()


def private_upload_to(instance, filename):
    # The stored name never comes from the uploader: client/<workspace>/<uuid>.<ext>
    ext = os.path.splitext(filename)[1].lower()
    return f"clients/{instance.workspace_id}/{instance._meta.app_label}/{uuid.uuid4().hex}{ext}"


class PrivateFile(models.Model):
    """Abstract: a client file stored privately. Subclasses must have ``workspace``."""

    class ScanStatus(models.TextChoices):
        CLEAN = scanning.CLEAN, "Scanned, clean"
        NOT_SCANNED = scanning.NOT_SCANNED, "Not scanned"

    file = models.FileField(storage=private_storage, upload_to=private_upload_to, max_length=255)
    original_name = models.CharField(max_length=255)
    file_format = models.CharField(max_length=10)
    file_kind = models.CharField(max_length=20)
    mime_type = models.CharField(max_length=100)
    file_size = models.PositiveBigIntegerField(default=0)
    scan_status = models.CharField(max_length=20, choices=ScanStatus.choices)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)

    # Workspace permission (staff) and portal permission (clients) needed to download.
    STAFF_PERMISSION = "view_client_profile"
    PORTAL_PERMISSION = "portal_view_content"

    class Meta:
        abstract = True

    def client_can_see(self) -> bool:
        """Whether client users of this workspace may download it. Override per model."""
        return False

    def attach(self, uploaded_file, allowed_kinds, *, user=None, max_bytes=None):
        """Validate, scan and set the file fields (call ``save()`` afterwards)."""
        kind, ext, mime, result = check_upload(uploaded_file, allowed_kinds, max_bytes=max_bytes)
        self.original_name = os.path.basename(uploaded_file.name)[:255]
        self.file_format, self.file_kind, self.mime_type = ext, kind, mime
        self.file_size = uploaded_file.size or 0
        self.scan_status = result.status
        self.uploaded_by = user
        uploaded_file.name = f"upload.{ext}"
        self.file = uploaded_file
        return result

    @property
    def is_image(self) -> bool:
        return self.file_kind == "image"

    @property
    def size_label(self) -> str:
        size = float(self.file_size)
        for unit in ("bytes", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
            size /= 1024
        return ""

    def download_name(self) -> str:
        base = os.path.splitext(self.original_name)[0] or "file"
        return f"{base}.{self.file_format}"

    def signed_url(self, *, inline=False) -> str:
        return signed_file_url(self, inline=inline)


def signed_file_url(obj, *, inline=False) -> str:
    inline = inline and obj.file_kind in INLINE_KINDS
    token = signing.dumps({"m": obj._meta.label_lower, "pk": str(obj.pk), "i": int(inline)}, salt=SALT, compress=True)
    return reverse("studio:file", kwargs={"token": token})


def load_signed(token: str) -> dict:
    """The payload of a valid, unexpired link. Raises signing.BadSignature (incl. SignatureExpired)."""
    return signing.loads(token, salt=SALT, max_age=getattr(settings, "FILE_LINK_TTL", 300))
