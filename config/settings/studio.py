"""Brown Creative Studio settings, layered over BrightBean's ``base.py``.

``base.py`` star-imports this module on its last line, so every settings module
(development, production, test) sees these values and can still override them.
Keeping the fork's settings here, rather than spread through upstream's file,
keeps upstream merges small. See CHANGES.md.

Nothing here may hold a secret: every value is either a public fact about the
deployment or read from the environment.
"""

from pathlib import Path

import environ

_BASE_DIR = Path(__file__).resolve().parent.parent.parent

# base.py has already loaded .env into os.environ (read_env), so a bare Env
# sees the same values without reading the file a second time.
_env = environ.Env()

# ── Studio identity ──────────────────────────────────────────────────────────
# Shown in page titles, emails and the django.contrib.sites record (allauth puts
# Site.name into email subjects). apps.about syncs that record after migrate.
SITE_NAME = _env("SITE_NAME", default="Brown Creative Studio").strip()

# Where users are told to write for help. Blank is allowed in development; the
# deploy check (about.W004) flags it in production.
SUPPORT_EMAIL = _env("SUPPORT_EMAIL", default="").strip()

# The studio's published policies. Blank serves in-app pages that say the policy
# has not been published yet; never BrightBean's documents.
TERMS_URL = _env("TERMS_URL", default="").strip()
PRIVACY_URL = _env("PRIVACY_URL", default="").strip()

# ── Corresponding source (AGPL-3.0 section 13) ───────────────────────────────
# Every studio and portal user sees a "Source code" link. It must lead to the
# source of the version that is actually running, so it is built from:
#   SOURCE_CODE_URL  the public fork (defaults to this repository on GitHub)
#   SOURCE_COMMIT    the commit the image was built from. The Docker build sets
#                    it from a build argument; without it, apps.about reads
#                    .git in the working copy (development only).
SOURCE_CODE_URL = (
    _env("SOURCE_CODE_URL", default="https://github.com/juliemangoes/brown-creative-studio").strip().rstrip("/")
)
SOURCE_COMMIT = _env("SOURCE_COMMIT", default="").strip()

# The project this fork is based on, credited on the source page.
UPSTREAM_PROJECT_NAME = "BrightBean Studio"
UPSTREAM_SOURCE_URL = "https://github.com/brightbeanxyz/brightbean-studio"
SOFTWARE_LICENSE = "AGPL-3.0"

# ── Studio facts (project settings) ──────────────────────────────────────────
# The studio's own time zone: shown beside each client's time when scheduling,
# and the default for new clients.
STUDIO_TIME_ZONE = _env("STUDIO_TIME_ZONE", default="America/Guatemala").strip()
# Where the studio issues invoices (ISO 3166-1 alpha-2).
STUDIO_COUNTRY = _env("STUDIO_COUNTRY", default="BZ").strip()
# Currencies invoices may use; the first is the default. Add USD with
# BILLING_CURRENCIES=BZD,USD.
BILLING_CURRENCIES = [c.strip().upper() for c in _env.list("BILLING_CURRENCIES", default=["BZD"]) if c.strip()]
# Billing providers (apps/billing/adapters.py). Bank transfer with proof-of-payment
# upload always works; "mock" providers are for testing and say so on screen.
BILLING_PAYMENT_PROVIDER = _env("BILLING_PAYMENT_PROVIDER", default="none").strip().lower()
BILLING_SIGNATURE_PROVIDER = _env("BILLING_SIGNATURE_PROVIDER", default="builtin").strip().lower()
BILLING_TAX_PROVIDER = _env("BILLING_TAX_PROVIDER", default="none").strip().lower()
# Languages a person can choose for the interface.
INTERFACE_LANGUAGES = [("en", "English"), ("es", "Español")]
LANGUAGES = INTERFACE_LANGUAGES
LANGUAGE_CODE = "en"
LOCALE_PATHS = [_BASE_DIR / "locale"]
# Spanish number formats as in Central America (config/formats/es).
FORMAT_MODULE_PATH = ["config.formats"]
# Today flags a client still in Onboarding after this many days.
STUDIO_ONBOARDING_STALL_DAYS = _env.int("STUDIO_ONBOARDING_STALL_DAYS", default=14)
# …or when nobody on the client's side has done anything for this many days.
STUDIO_ONBOARDING_IDLE_DAYS = _env.int("STUDIO_ONBOARDING_IDLE_DAYS", default=5)

# Private client files (brand assets, onboarding uploads). On local storage they
# live outside MEDIA_ROOT and are never served directly; on S3 they are private
# objects under private/. Links expire after FILE_LINK_TTL seconds.
PRIVATE_MEDIA_ROOT = _env("PRIVATE_MEDIA_ROOT", default=str(_BASE_DIR / "private_media"))
PRIVATE_FILE_MAX_BYTES = _env.int("PRIVATE_FILE_MAX_BYTES", default=50 * 1024 * 1024)
FILE_LINK_TTL = _env.int("FILE_LINK_TTL", default=300)
# Malware scanning of uploads: "clamd" in production, "mock" (EICAR test string
# only, nothing else is scanned) in development. See apps/studio/scanning.py.
MALWARE_SCAN_BACKEND = _env("MALWARE_SCAN_BACKEND", default="mock").strip().lower()
CLAMD_HOST = _env("CLAMD_HOST", default="clamav").strip()
CLAMD_PORT = _env.int("CLAMD_PORT", default=3310)

# ── Publishing (apps/publishing, Phase 6) ────────────────────────────────────
# TikTok Direct Post needs TikTok's audit of the app. Until it is approved and
# this is turned on, TikTok posts are posted by hand: the app prepares the
# approved video and caption, reminds the assigned person and waits for the
# live link (assisted posting). Nothing is sent to TikTok's API.
TIKTOK_DIRECT_POST_ENABLED = _env.bool("TIKTOK_DIRECT_POST_ENABLED", default=False)
# How long before its time a hand-posted post becomes "Manual action needed".
PUBLISHING_MANUAL_LEAD_MINUTES = _env.int("PUBLISHING_MANUAL_LEAD_MINUTES", default=60)
# Pre-publish checks look at posts due in the next PUBLISHING_CHECK_HOURS and
# alert the team about a problem once it is within PUBLISHING_ALERT_HOURS.
PUBLISHING_CHECK_HOURS = _env.int("PUBLISHING_CHECK_HOURS", default=48)
PUBLISHING_ALERT_HOURS = _env.int("PUBLISHING_ALERT_HOURS", default=24)
# Remind the person again when a hand-posted post is this many minutes late.
PUBLISHING_MANUAL_REMINDER_MINUTES = _env.int("PUBLISHING_MANUAL_REMINDER_MINUTES", default=60)

# ── AI drafting (apps/reports/ai.py, Phase 7) ────────────────────────────────
# "mock" writes drafts from the figures with fixed sentences (no service is
# called); "claude" uses Anthropic's API with the key in ANTHROPIC_API_KEY;
# "none" switches drafting off. Drafts always need a person to edit and approve.
AI_PROVIDER = _env("AI_PROVIDER", default="mock").strip().lower()
AI_MODEL = _env("AI_MODEL", default="claude-opus-5-5").strip()

# ── Research (apps/research, Phase 8) ───────────────────────────────────────
# Where "Emerging now" finds trends: "none" (default: only what the team saves,
# plus a link to TikTok Creative Center) or "mock" (labelled test data). Only
# official APIs, licensed providers, web search APIs or hand-saved items.
RESEARCH_PROVIDER = _env("RESEARCH_PROVIDER", default="none").strip().lower()
# Day the weekly brief is prepared (0 = Monday), in the studio's time zone.
RESEARCH_BRIEF_WEEKDAY = _env.int("RESEARCH_BRIEF_WEEKDAY", default=0)
