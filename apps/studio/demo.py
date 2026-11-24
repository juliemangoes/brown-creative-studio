"""Demo data: a studio with a restaurant, a skincare brand and a real estate company.

Everything here is invented. Social accounts are placeholders with no tokens:
they cannot publish, and the publishing worker should not be run against them
(a due post on a demo account fails, which is also how "failed" is shown).
Passwords are generated on each run and printed once, never stored here.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

DEMO_ORG_NAME = "Brown Creative Studio (demo)"
DEMO_EMAIL_DOMAIN = "brown-demo.example"  # .example is reserved (RFC 2606): never a real inbox


@dataclass
class DemoResult:
    organization: object
    logins: list[tuple[str, str, str]] = field(default_factory=list)  # (role, email, password)
    portal_links: list[tuple[str, str, str]] = field(default_factory=list)  # (client, email, url)


def demo_exists() -> bool:
    from apps.organizations.models import Organization

    return Organization.objects.filter(name=DEMO_ORG_NAME).exists()


def remove_demo() -> None:
    """Delete the demo studio and demo users only (matched by exact name and the reserved domain)."""
    from apps.accounts.models import User
    from apps.organizations.models import Organization

    for org in Organization.objects.filter(name=DEMO_ORG_NAME):
        org.delete()
    User.objects.filter(email__iendswith=f"@{DEMO_EMAIL_DOMAIN}").delete()


class _Builder:
    def __init__(self, now=None):
        self.now = now or timezone.now()
        self.tz = ZoneInfo(settings.STUDIO_TIME_ZONE)
        self.result: DemoResult | None = None

    # ── people ──────────────────────────────────────────────────────────
    def user(self, local, name, role_label):
        from apps.accounts.models import User
        from apps.members.models import OrgMembership
        from apps.studio.services import drop_default_org

        password = secrets.token_urlsafe(12)
        email = f"{local}@{DEMO_EMAIL_DOMAIN}"
        user = User.objects.create_user(email=email, password=password, name=name, tos_accepted_at=self.now)
        OrgMembership.objects.create(
            user=user, organization=self.org, org_role="owner" if role_label == "Studio owner" else "member"
        )
        drop_default_org(user, self.org)
        self.result.logins.append((role_label, email, password))
        return user

    def member(self, user, workspace, role, custom_role=None):
        from apps.members.models import WorkspaceMembership

        return WorkspaceMembership.objects.create(
            user=user, workspace=workspace, workspace_role=role, custom_role=custom_role
        )

    # ── content ─────────────────────────────────────────────────────────
    def at(self, days, hour=11, minute=0):
        local_day = (self.now.astimezone(self.tz) + timedelta(days=days)).date()
        return datetime.combine(local_day, time(hour, minute), tzinfo=self.tz)

    def account(self, workspace, platform, name, handle, followers, status="connected"):
        from apps.social_accounts.models import SocialAccount

        account = SocialAccount(
            workspace=workspace,
            platform=platform,
            account_platform_id=f"demo-{uuid.uuid4().hex[:12]}",
            account_name=name,
            account_handle=handle,
            follower_count=followers,
            connection_status=status,
            last_error="" if status == "connected" else "Demo account: the platform revoked access.",
        )
        account._skip_analytics_backfill = True  # no provider calls for placeholder accounts
        account.save()
        return account

    def post(self, workspace, accounts, title, caption, when, status, *, author, error="", notes=""):
        from apps.composer.models import PlatformPost, Post

        published = status == "published"
        post = Post.objects.create(
            workspace=workspace,
            author=author,
            title=title,
            caption=caption,
            internal_notes=notes,
            scheduled_at=when,
            published_at=when if published else None,
        )
        for account in accounts:
            PlatformPost.objects.create(
                post=post,
                social_account=account,
                status=status,
                scheduled_at=when,
                published_at=when if published else None,
                platform_post_id=f"demo-{uuid.uuid4().hex[:10]}" if published else "",
                publish_error=error,
            )
        return post

    def approval(self, post, user, action, comment=""):
        from apps.approvals.models import ApprovalAction

        return ApprovalAction.objects.create(post=post, user=user, action=action, comment=comment)

    def comment(self, post, author, body, visibility):
        from apps.approvals.models import PostComment

        return PostComment.objects.create(post=post, author=author, body=body, visibility=visibility)

    def insights(self, account, followers_now, daily_reach):
        from apps.analytics.models import AccountInsightsSnapshot

        rows = []
        today = timezone.localdate()
        for back in range(28, 0, -1):
            day = today - timedelta(days=back)
            wobble = (back * 37) % 11 - 5
            rows += [
                AccountInsightsSnapshot(
                    social_account=account, metric_key="followers", date=day, value=followers_now - back * 6
                ),
                AccountInsightsSnapshot(
                    social_account=account, metric_key="reach", date=day, value=max(0, daily_reach + wobble * 40)
                ),
                AccountInsightsSnapshot(
                    social_account=account, metric_key="views", date=day, value=max(0, daily_reach * 2 + wobble * 70)
                ),
                AccountInsightsSnapshot(
                    social_account=account, metric_key="likes", date=day, value=max(0, daily_reach // 12 + wobble)
                ),
            ]
        AccountInsightsSnapshot.objects.bulk_create(rows)

    def post_insights(self, post, reach):
        from apps.analytics.models import PostInsightsSnapshot

        rows = []
        for pp in post.platform_posts.all():
            day = timezone.localdate(pp.published_at)
            for key, value in (
                ("reach", reach),
                ("likes", reach // 14),
                ("comments", reach // 160),
                ("saves", reach // 60),
                ("shares", reach // 120),
            ):
                rows.append(PostInsightsSnapshot(platform_post=pp, metric_key=key, date=day, value=value))
        PostInsightsSnapshot.objects.bulk_create(rows)

    # ── the studio ──────────────────────────────────────────────────────
    @transaction.atomic
    def build(self) -> DemoResult:
        from apps.client_portal.models import MagicLinkToken
        from apps.clients.models import ClientProfile, Contact, Retainer, ServiceLine
        from apps.clients.services import create_client
        from apps.organizations.models import Organization
        from apps.studio.permissions import ensure_client_roles

        self.org = Organization.objects.create(name=DEMO_ORG_NAME, default_timezone=settings.STUDIO_TIME_ZONE)
        self.result = DemoResult(organization=self.org)

        owner = self.user("owner", "Alex Rivera", "Studio owner")
        team = self.user("maya", "Maya Torres", "Team member")
        freelancer = self.user("diego", "Diego Cruz", "Freelancer")

        restaurant = create_client(
            self.org,
            owner,
            name="Casa Maya Restaurant",
            stage=ClientProfile.Stage.ACTIVE,
            services=["social_media"],
            contact_name="Ana Pérez",
            contact_email=f"ana@{DEMO_EMAIL_DOMAIN}",
            industry="Restaurant",
        )
        skincare = create_client(
            self.org,
            owner,
            name="Luma Skincare",
            stage=ClientProfile.Stage.ACTIVE,
            services=["social_media", "branding"],
            contact_name="Lia Chen",
            contact_email=f"lia@{DEMO_EMAIL_DOMAIN}",
            industry="Skincare",
        )
        realty = create_client(
            self.org,
            owner,
            name="Vista Realty",
            stage=ClientProfile.Stage.ONBOARDING,
            services=["brand_strategy", "social_media"],
            contact_name="Marco Ruiz",
            contact_email=f"marco@{DEMO_EMAIL_DOMAIN}",
            industry="Real estate",
        )
        roles = ensure_client_roles(self.org)

        for ws in (restaurant, skincare, realty):
            self.member(team, ws, "manager")
        self.member(freelancer, restaurant, "contributor")

        # Profiles, retainers and extra contacts.
        ClientProfile.objects.filter(workspace=restaurant).update(
            legal_name="Casa Maya Ltd.",
            city="San Ignacio",
            country="BZ",
            portal_language="es",
            started_on=self.now.date() - timedelta(days=210),
            website="https://casamaya.example",
            internal_notes="Owner answers fastest on WhatsApp. Busy season Dec–Apr.",
        )
        ClientProfile.objects.filter(workspace=skincare).update(
            legal_name="Luma Skincare Co.",
            city="Belize City",
            country="BZ",
            portal_language="en",
            started_on=self.now.date() - timedelta(days=95),
            website="https://lumaskin.example",
            internal_notes="Launching a sunscreen line in Q1; brand refresh in progress.",
        )
        ClientProfile.objects.filter(workspace=realty).update(
            city="Belmopan",
            country="BZ",
            portal_language="en",
            lead_source="Referral from Casa Maya",
            stage_changed_at=self.now - timedelta(days=20),
        )
        month_start = self.now.date().replace(day=1)
        Retainer.objects.create(
            workspace=restaurant,
            name="Social media retainer",
            monthly_fee=Decimal("1800.00"),
            starts_on=month_start - timedelta(days=180),
            posts_per_month=16,
            reels_per_month=4,
            stories_per_month=20,
            shoot_days_per_month=1,
            revision_rounds=2,
            scope_notes="Instagram and Facebook. One shoot day a month at the restaurant.",
        )
        Retainer.objects.create(
            workspace=skincare,
            name="Social and brand retainer",
            monthly_fee=Decimal("2400.00"),
            starts_on=month_start - timedelta(days=90),
            posts_per_month=12,
            reels_per_month=4,
            stories_per_month=12,
            shoot_days_per_month=1,
            revision_rounds=2,
            scope_notes="Instagram and TikTok, plus the brand refresh (logo, palette, packaging labels).",
        )
        ServiceLine.objects.filter(workspace=skincare, kind="branding").update(
            started_on=month_start - timedelta(days=30)
        )
        Contact.objects.create(
            workspace=restaurant,
            name="Luis Pérez",
            title="Co-owner",
            email=f"luis@{DEMO_EMAIL_DOMAIN}",
            is_approver=True,
        )
        Contact.objects.create(
            workspace=skincare, name="Noor Ali", title="Finance", email=f"noor@{DEMO_EMAIL_DOMAIN}", is_billing=True
        )

        # Client users, with the portal role presets.
        clients = [
            (restaurant, "ana", "Ana Pérez", "Client admin"),
            (restaurant, "luis", "Luis Pérez", "Client reviewer"),
            (skincare, "lia", "Lia Chen", "Client admin"),
            (skincare, "noor", "Noor Ali", "Client billing contact"),
            (realty, "marco", "Marco Ruiz", "Client admin"),
        ]
        client_users = {}
        for ws, local, name, preset in clients:
            user = self.user(local, name, f"{preset} · {ws.name}")
            self.member(user, ws, "client", roles[preset])
            Contact.objects.for_workspace(ws.id).filter(email=user.email).update(user=user)
            client_users[local] = user
            token = MagicLinkToken.objects.create(user=user, workspace=ws)
            app_url = getattr(settings, "APP_URL", "http://localhost:8000").rstrip("/")
            self.result.portal_links.append((ws.name, user.email, f"{app_url}/portal/{token.token}/"))

        # Accounts (placeholders).
        cm_ig = self.account(restaurant, "instagram", "Casa Maya", "casamaya.bz", 4820)
        cm_fb = self.account(restaurant, "facebook", "Casa Maya Restaurant", "casamayabz", 7310)
        lu_ig = self.account(skincare, "instagram", "Luma Skincare", "luma.skin", 12650)
        lu_tt = self.account(skincare, "tiktok", "Luma Skincare", "lumaskin", 3900, status="token_expiring")
        self.account(skincare, "facebook", "Luma Skincare Page", "lumaskincare", 2100, status="disconnected")
        self.insights(cm_ig, 4820, 1400)
        self.insights(lu_ig, 12650, 3900)

        self._restaurant_posts(restaurant, [cm_ig, cm_fb], owner, team, freelancer, client_users)
        self._skincare_posts(skincare, [lu_ig], lu_tt, owner, team, client_users)
        self._client_setup(restaurant, skincare, realty, owner, team, freelancer, client_users)
        self.cast = {
            "restaurant": restaurant,
            "skincare": skincare,
            "realty": realty,
            "owner": owner,
            "team": team,
            "freelancer": freelancer,
            "clients": client_users,
            "cm_accounts": [cm_ig, cm_fb],
        }
        return self.result

    def _restaurant_posts(self, ws, accounts, owner, team, freelancer, client_users):
        from apps.approvals.models import ApprovalAction, PostComment

        ig = accounts[:1]
        history = [
            (-20, "Hudut Sunday", "Our hudut with fresh snapper is back every Sunday.", 2600),
            (-16, "Meet chef Rosa", "Twenty years in this kitchen and still tasting every pot.", 3400),
            (-12, "Rainy-day tamales", "Nothing beats tamales when it pours.", 1900),
            (-8, "Live marimba Friday", "Marimba from 7pm. Book a table.", 4100),
            (-4, "Behind the bar: hibiscus agua fresca", "Brewed every morning.", 2200),
            (-2, "Weekend brunch menu", "Fry jacks, beans and eggs your way.", 3100),
        ]
        for days, title, caption, reach in history:
            post = self.post(ws, accounts, title, caption, self.at(days, 12), "published", author=team)
            self.approval(post, client_users["ana"], ApprovalAction.ActionType.APPROVED)
            self.post_insights(post, reach)

        late = self.post(
            ws,
            ig,
            "Taco Tuesday reel",
            "Three tacos, one price, every Tuesday.",
            self.at(0, 9, 30) - timedelta(hours=26),
            "pending_client",
            author=freelancer,
        )
        self.approval(late, team, ApprovalAction.ActionType.SUBMITTED)
        self.comment(
            late,
            team,
            "Ana, this one went out of date yesterday; can you approve today so we post it next Tuesday?",
            PostComment.Visibility.EXTERNAL,
        )

        soon = self.post(
            ws,
            accounts,
            "New lunch specials",
            "Five lunch plates under BZ$20 this week.",
            self.at(2, 11, 30),
            "pending_client",
            author=team,
        )
        self.approval(soon, team, ApprovalAction.ActionType.SUBMITTED)

        review = self.post(
            ws,
            ig,
            "Kitchen tour carousel",
            "A walk through the kitchen at 6am.",
            self.at(4, 18),
            "pending_review",
            author=freelancer,
            notes="Swap slide 3, the photo is soft.",
        )
        self.comment(
            review,
            team,
            "Slide 3 is soft; reshoot at the next shoot day. Do not mention the new supplier yet.",
            PostComment.Visibility.INTERNAL,
        )

        changes = self.post(
            ws,
            accounts,
            "Christmas menu teaser",
            "Our Christmas menu is almost ready...",
            self.at(9, 12),
            "changes_requested",
            author=team,
        )
        self.approval(
            changes,
            client_users["luis"],
            ApprovalAction.ActionType.CHANGES_REQUESTED,
            "Please use the photo with the black cake, not the ham.",
        )
        self.comment(
            changes,
            client_users["luis"],
            "Please use the photo with the black cake, not the ham.",
            PostComment.Visibility.EXTERNAL,
        )

        for days, title, caption in [
            (1, "Friday marimba night", "Marimba is back this Friday from 7pm."),
            (3, "Seafood platter close-up", "Lobster season is open."),
        ]:
            post = self.post(ws, accounts, title, caption, self.at(days, 17), "scheduled", author=team)
            self.approval(post, client_users["ana"], ApprovalAction.ActionType.APPROVED)

        approved = self.post(
            ws,
            ig,
            "Staff picks: horchata",
            "Our team's favourite drink.",
            self.at(6, 15),
            "approved",
            author=freelancer,
        )
        self.approval(approved, client_users["ana"], ApprovalAction.ActionType.APPROVED)
        for days, title in [(12, "Holiday hours"), (16, "Gift cards are here"), (21, "New Year's Eve dinner")]:
            self.post(ws, ig, title, "", self.at(days, 12), "draft", author=team)

    def _skincare_posts(self, ws, accounts, tiktok, owner, team, client_users):
        from apps.approvals.models import ApprovalAction, PostComment

        history = [
            (-18, "Morning routine in 3 steps", "Cleanse, treat, protect.", 6200),
            (-11, "Ingredient spotlight: niacinamide", "What it does and who it's for.", 8800),
            (-5, "Customer of the month", "Thank you, Marisol, for sharing your results.", 5400),
        ]
        for days, title, caption, reach in history:
            post = self.post(ws, accounts, title, caption, self.at(days, 10), "published", author=team)
            self.approval(post, client_users["lia"], ApprovalAction.ActionType.APPROVED)
            self.post_insights(post, reach)

        failed = self.post(
            ws,
            accounts,
            "Sunscreen myths",
            "Myth 1: you don't need sunscreen on cloudy days.",
            self.at(0, 8) - timedelta(hours=3),
            "failed",
            author=team,
            error="Instagram rejected the media: the image aspect ratio is not supported (demo).",
        )
        self.approval(failed, client_users["lia"], ApprovalAction.ActionType.APPROVED)

        launch = self.post(
            ws,
            accounts + [tiktok],
            "Serum launch countdown",
            "Something new is coming on the 1st.",
            self.at(1, 10),
            "pending_client",
            author=team,
        )
        self.approval(launch, team, ApprovalAction.ActionType.SUBMITTED)
        self.comment(
            launch,
            owner,
            "Client pays net 30; hold the boosted version until the invoice clears.",
            PostComment.Visibility.INTERNAL,
        )

        scheduled = self.post(
            ws,
            accounts,
            "Night routine reel",
            "Wind down with three products.",
            self.at(5, 19),
            "scheduled",
            author=team,
        )
        self.approval(scheduled, client_users["lia"], ApprovalAction.ActionType.APPROVED)
        self.post(ws, accounts, "Brand refresh sneak peek", "", self.at(14, 10), "draft", author=owner)

    # ── Phase 3: projects, brand work and onboarding ─────────────────────
    def _logo(self, name, background, ink, *, png=False):
        from django.core.files.uploadedfile import SimpleUploadedFile

        if png:
            import io

            from PIL import Image, ImageDraw

            image = Image.new("RGB", (480, 160), background)
            ImageDraw.Draw(image).text((32, 64), name, fill=ink)
            buf = io.BytesIO()
            image.save(buf, "PNG")
            return SimpleUploadedFile(f"{name.lower().replace(' ', '-')}.png", buf.getvalue())
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80" width="240" height="80">'
            f'<rect width="240" height="80" rx="6" fill="{background}"/>'
            f'<text x="20" y="50" font-family="Georgia, serif" font-size="26" fill="{ink}">{name}</text></svg>'
        )
        return SimpleUploadedFile(f"{name.lower().replace(' ', '-')}.svg", svg.encode())

    def _client_setup(self, restaurant, skincare, realty, owner, team, freelancer, client_users):
        from datetime import timedelta as td

        from apps.brand.models import BrandStage
        from apps.brand.services import add_asset, link_brand_project, profile_for, send_for_signoff, sign_off
        from apps.client_onboarding.models import OnboardingRun, OnboardingStep
        from apps.client_onboarding.services import build_flow, touch
        from apps.projects.models import Task
        from apps.projects.services import create_from_template, ensure_builtin_templates, month_label, month_start

        today = timezone.localdate()
        templates = ensure_builtin_templates(self.org)

        # Casa Maya: this month's retainer project, partly done, one task overdue, one for the freelancer.
        social = create_from_template(
            restaurant,
            templates["social_retainer"],
            name=f"{month_label(today)} social",
            starts_on=today - td(days=12),
            owner=team,
            created_by=owner,
            period_start=month_start(today),
            client_summary="This month's plan, shoot, approvals and report.",
        )
        for key in ("plan", "plan_approval", "shoot_prep", "shoot"):
            Task.objects.filter(project=social, template_key=key).update(
                status=Task.Status.DONE, completed_at=self.now - td(days=3)
            )
        Task.objects.filter(project=social, template_key="edit").update(
            assignee=freelancer, status=Task.Status.IN_PROGRESS
        )
        Task.objects.filter(project=social, template_key="captions").update(assignee=team, due_on=today - td(days=1))
        brand = profile_for(restaurant)
        brand.purpose = "Home cooking from Cayo, served like family."
        brand.positioning = (
            "The warmest table in San Ignacio: Belizean and Maya dishes made the way the owners' grandmother did."
        )
        brand.values, brand.personality = ["Generosity", "Family", "Craft"], ["Warm", "Lively", "Unpretentious"]
        brand.audiences = [
            {"name": "Locals", "description": "Lunch regulars and Sunday families"},
            {"name": "Visitors", "description": "Travellers looking for real food"},
        ]
        brand.voice = "Warm, a little playful, never fancy. Spanish and English mixed the way people speak here."
        brand.voice_do, brand.voice_dont = ["Name the cook", "Talk about where food comes from"], ["Exotic", "Fusion"]
        brand.tagline = "Comida de casa"
        brand.key_messages = ["Made from scratch every morning", "Recipes from three generations"]
        brand.colors = [
            {"name": "Cacao", "hex": "#6B4F3A", "role": "Primary"},
            {"name": "Masa", "hex": "#F2E6D0", "role": "Backgrounds"},
            {"name": "Achiote", "hex": "#B5472B", "role": "Accents"},
        ]
        brand.fonts = [
            {"name": "Instrument Serif", "usage": "Headings and menus"},
            {"name": "Geist", "usage": "Everything else"},
        ]
        brand.internal_notes = "Owner dislikes purple; never discount on social."
        brand.is_published, brand.published_at = True, self.now - td(days=60)
        brand.save()
        for upload, variant in (
            (self._logo("Casa Maya", "#6B4F3A", "#F2E6D0"), "Primary"),
            (self._logo("Casa Maya", "#6B4F3A", "#F2E6D0", png=True), "Primary"),
        ):
            add_asset(
                restaurant, upload, user=owner, kind="logo", title="Casa Maya", variant=variant, client_visible=True
            )
        OnboardingRun.objects.create(
            workspace=restaurant,
            status=OnboardingRun.Status.COMPLETED,
            services=["social_media"],
            created_by=owner,
            started_at=self.now - td(days=220),
            completed_at=self.now - td(days=205),
        )

        # Luma: a brand refresh in progress (discovery and research signed off, strategy waiting).
        brand_project = create_from_template(
            skincare,
            templates["brand"],
            name="Luma brand refresh",
            starts_on=today - td(days=24),
            owner=owner,
            created_by=owner,
            client_summary="Seven stages, each signed off by you in the Brand Hub.",
        )
        stages = {s.key: s for s in link_brand_project(brand_project)}
        for key in ("discovery", "research"):
            send_for_signoff(
                stages[key], owner, f"{stages[key].get_key_display()} findings, summarised in the attached deck."
            )
            sign_off(stages[key], client_users["lia"])
        Task.objects.filter(
            project=brand_project,
            template_key__in=["discovery_0", "discovery_1", "discovery_2", "research_0", "research_1", "research_2"],
        ).update(status=Task.Status.DONE, completed_at=self.now - td(days=5))
        strategy = BrandStage.objects.get(workspace=skincare, key="strategy")
        send_for_signoff(
            strategy,
            owner,
            "Positioning: clean, tropical-proof skincare made in Belize. Values, personality and two audience profiles are in the deck.",
        )
        luma_brand = profile_for(skincare)
        luma_brand.positioning = "Clean skincare made for heat and humidity, made in Belize."
        luma_brand.values = ["Honest ingredients", "Made locally", "Kind to skin"]
        luma_brand.save()

        # Vista Realty: onboarding started 20 days ago and gone quiet.
        flow = build_flow(self.org, ["brand_strategy", "social_media"])
        run = OnboardingRun.objects.create(
            workspace=realty,
            services=["brand_strategy", "social_media"],
            welcome_message=flow["welcome_message"],
            timeline=flow["timeline"],
            questions=flow["questions"],
            reminder_days=flow["reminder_days"],
            created_by=owner,
            started_at=self.now - td(days=20),
            reminders_sent=3,
            next_reminder_at=self.now + td(days=1),
            kickoff_slots=[
                (self.now + td(days=d)).replace(hour=15, minute=0, second=0, microsecond=0).isoformat() for d in (3, 4)
            ],
        )
        done_keys = {"welcome", "proposal", "details"}
        for position, key in enumerate(flow["steps"]):
            OnboardingStep.objects.create(
                run=run,
                key=key,
                position=position,
                status="done"
                if key in done_keys
                else (
                    "waiting_studio"
                    if key in ("contract", "deposit")
                    else ("in_progress" if key == "questionnaire" else "todo")
                ),
                completed_at=self.now - td(days=18) if key in done_keys else None,
                blocker="Contract with their lawyer since 4 Nov" if key == "contract" else "",
                data={
                    "answers": {
                        "goals": {
                            "value": "Become the first name families think of in Belmopan.",
                            "by_name": "Marco Ruiz",
                            "at": (self.now - td(days=16)).isoformat(),
                        }
                    }
                }
                if key == "questionnaire"
                else {},
            )
        touch(run, client_users["marco"], action=True)
        OnboardingRun.objects.filter(pk=run.pk).update(last_client_activity_at=self.now - td(days=16))
        run.participants.update(
            invited_at=self.now - td(days=20),
            last_seen_at=self.now - td(days=16),
            last_action_at=self.now - td(days=16),
            reminders_sent=3,
        )

    # ── Phase 4: planning, shoots, rights, one post from idea to approval ──
    def _photo(self, name, background, ink=(255, 255, 255)):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (1080, 1350), background)
        ImageDraw.Draw(image).text((60, 1240), name, fill=ink)
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")

    def content_operations(self):
        """Run with the real review rules on (outside ``bypass``), each step at its own invented time."""
        from unittest import mock

        from apps.approvals import services as approvals
        from apps.assets.models import AssetRights
        from apps.clients.models import Contact
        from apps.composer.models import Idea
        from apps.media_library.services import create_asset
        from apps.planning.models import Campaign, ContentPlan, KeyDate
        from apps.planning.services import ensure_holidays, plan_idea, quick_plan
        from apps.shoots import services as shoots
        from apps.shoots.models import Shoot

        c = self.cast
        ws, luma, owner, team, freelancer = c["restaurant"], c["skincare"], c["owner"], c["team"], c["freelancer"]
        ana = c["clients"]["ana"]
        today = timezone.localdate(self.now)
        td = timedelta

        def at(days, hour=10, minute=0):
            moment = min(self.at(days, hour, minute), self.now - td(minutes=5))  # never in the future
            return mock.patch.object(timezone, "now", lambda: moment)

        # Key dates and a campaign.
        ensure_holidays(self.org, [today.year, today.year + 1])
        KeyDate.objects.create(
            organization=self.org, workspace=ws, date=today + td(days=18), title="Casa Maya turns 10", kind="client"
        )
        KeyDate.objects.create(
            organization=self.org, workspace=luma, date=today + td(days=25), title="Night serum launch", kind="client"
        )
        Campaign.objects.create(
            workspace=ws,
            name="Christmas menu",
            objective="Fill December dinner bookings.",
            starts_on=today + td(days=4),
            ends_on=today + td(days=34),
            color="#8A6A4F",
            created_by=owner,
        )

        # One post, idea to client approval, over the last eight days.
        with at(-8, 9, 15):
            idea = Idea.objects.create(
                workspace=ws,
                author=team,
                title="Mole negro, step by step",
                description="Rosa toasting the chiles, grinding, the first spoonful. A reel for the Christmas menu.",
                tags=["menu", "chef"],
            )
        with at(-7, 11, 40):
            plan = plan_idea(idea, team, target_date=today + td(days=6), format=ContentPlan.Format.REEL)
            plan.hook = "Rosa's hands toasting the chiles"
            plan.objective = ContentPlan.Objective.SALES
            plan.save(update_fields=["hook", "objective"])
        with at(-5, 16, 5):
            done_shoot = Shoot.objects.create(
                workspace=ws,
                title="November menu shoot",
                date=today - td(days=3),
                call_time=time(8, 0),
                wrap_time=time(12, 0),
                locations=["Casa Maya kitchen"],
                equipment=["Sony FX3", "Aputure 300d"],
                talent=[{"name": "Rosa Chi", "role": "chef", "release": True}],
                status=Shoot.Status.CONFIRMED,
                prep_sent_at=self.now - td(days=5),
                created_by=owner,
            )
            done_shoot.crew.add(owner, freelancer)
            shot = shoots.shots_from_posts(done_shoot, [plan.post], owner)[0]
            shoots.add_shot(done_shoot, description="Steam off the comal, slow motion", priority="nice", user=owner)
        with at(-3, 9, 50):
            shoots.apply_ops(done_shoot, freelancer, [{"op": "status", "shot": str(shot.id), "status": "captured"}])
            shoots.apply_ops(
                done_shoot,
                freelancer,
                [
                    {
                        "op": "idea",
                        "id": str(uuid.uuid4()),
                        "text": "Don Pedro tasting at the pass",
                        "status": "captured",
                    }
                ],
            )
        with at(-2, 14, 20):
            photo = self._photo(f"{shot.code}_mole-negro.png", (74, 44, 30))
            matched, _, refused = shoots.upload_and_match(done_shoot, team, [photo])
            if refused:  # a real scanner that is not reachable: the file was made here, so attach it directly
                photo.seek(0)
                asset = create_asset(self.org, ws, photo, team, title=photo.name, source="shoot")
                AssetRights.objects.create(media_asset=asset, shoot=done_shoot, updated_by=team)
                shoots.attach_to_shot(shot, asset, team)
            done_shoot.status = Shoot.Status.DONE
            done_shoot.save(update_fields=["status", "updated_at"])
            AssetRights.objects.filter(shoot=done_shoot).update(talent_release=True, release_reference="Rosa Chi, 2026")
        with at(-2, 16, 0):
            plan.post.caption = (
                "Three days, twenty-six ingredients, one pot. Rosa's mole negro is on the Christmas menu."
            )
            plan.post.save(update_fields=["caption", "updated_at"])
            approvals.submit_for_review(plan.post, team, ws)
        with at(-1, 10, 30):
            approvals.approve_post(plan.post, owner, ws)
        with at(0, 8, 45):
            approvals.approve_post(plan.post, ana, ws)

        # Planned posts still to shoot, and next week's shoot (not ready yet: prep not sent, a release missing).
        needs = []
        for days, title, fmt in (
            (9, "Christmas table for six", ContentPlan.Format.PHOTO),
            (12, "Kitchen at dawn", ContentPlan.Format.REEL),
            (15, "Ponche, the family recipe", ContentPlan.Format.CAROUSEL),
            (20, "Ten years of Casa Maya", ContentPlan.Format.REEL),
        ):
            needs.append(
                quick_plan(
                    ws,
                    team,
                    title=title,
                    target_date=today + td(days=days),
                    format=fmt,
                    stage=ContentPlan.Stage.SHOT_NEEDED,
                    accounts=c["cm_accounts"],
                )
            )
        contact = Contact.objects.for_workspace(ws.id).filter(email=ana.email).first()
        december = Shoot.objects.create(
            workspace=ws,
            title="December menu shoot",
            date=today + td(days=4),
            call_time=time(7, 30),
            wrap_time=time(12, 30),
            locations=["Casa Maya kitchen", "Patio"],
            equipment=["Sony FX3", "Aputure 300d", "Tripod"],
            talent=[{"name": "Rosa Chi", "role": "chef", "release": True}, {"name": "Don Pedro", "role": "owner"}],
            run_of_show=[
                {"time": "07:30", "item": "Set up in the kitchen"},
                {"time": "08:00", "item": "Christmas table, overhead"},
                {"time": "10:00", "item": "Patio, ponche pour"},
            ],
            prep_checklist=[
                {"text": "Christmas table laid for six by 07:30", "done": False},
                {"text": "Ponche made the night before", "done": False},
            ],
            client_contact=contact,
            client_notes="Park behind the kitchen. Ana meets the crew at the side door.",
            notes="Don Pedro's release still to sign. Ask Ana for the new menu card.",
            created_by=owner,
        )
        december.crew.add(owner, freelancer)
        shoots.shots_from_posts(december, [p.post for p in needs[:2]], owner)
        shoots.add_shot(december, description="Menu card on the table", priority="nice", user=owner)

        # A Luma shoot the same morning with the same camera and the owner: a booking conflict to resolve.
        luma_shoot = Shoot.objects.create(
            workspace=luma,
            title="Night serum flat-lays",
            date=today + td(days=4),
            call_time=time(11, 0),
            wrap_time=time(14, 0),
            locations=["Studio table"],
            equipment=["Sony FX3"],
            created_by=owner,
        )
        luma_shoot.crew.add(owner)

        # A licensed photo whose rights end before the post that uses it.
        licensed = create_asset(
            self.org,
            ws,
            self._photo("rosa-portrait-licensed.png", (122, 92, 70)),
            owner,
            title="Rosa portrait (licensed)",
            tags=["licensed"],
            source="upload",
        )
        AssetRights.objects.create(
            media_asset=licensed,
            source=AssetRights.Source.LICENSED,
            usage=AssetRights.Usage.ORGANIC,
            licence="Licensed from the photographer for organic posts until the date below.",
            talent_release=True,
            expires_on=today + td(days=10),
            updated_by=owner,
        )
        quick_plan(
            ws,
            team,
            title="Rosa, twenty years at the stove",
            target_date=today + td(days=17),
            format=ContentPlan.Format.PHOTO,
            accounts=c["cm_accounts"][:1],
            media_asset_ids=[licensed.id],
        )

    # ── Phase 5: billing, portal messages, languages ─────────────────────
    def billing(self):
        """Invoices in every state, proposals and contracts, a payment to confirm, and client messages."""
        from apps.billing import services as bill
        from apps.billing.models import CatalogItem, Invoice, Payment
        from apps.clients.models import ClientProfile
        from apps.conversations import services as talk
        from apps.studio.languages import set_language

        c = self.cast
        casa, luma, vista, owner = c["restaurant"], c["skincare"], c["realty"], c["owner"]
        ana, lia, marco = c["clients"]["ana"], c["clients"]["lia"], c["clients"]["marco"]
        today = timezone.localdate(self.now)
        td = timedelta

        settings_row = bill.settings_for(self.org)
        settings_row.legal_name = "Brown Creative Studio (demo)"
        settings_row.address = "Burns Avenue\nSan Ignacio, Cayo\nBelize"
        settings_row.tax_id = "DEMO-000000"
        settings_row.email = f"billing@{DEMO_EMAIL_DOMAIN}"
        settings_row.bank_details = "Demo Bank Belize · Brown Creative Studio · account 000-000000 (invented)"
        settings_row.payment_instructions = "Transfers usually arrive within one working day."
        settings_row.tax_name, settings_row.tax_rate = "GST (demo)", Decimal("12.5")
        settings_row.footer_note = "Demo data: invented studio, accounts and amounts."
        settings_row.save()
        catalog = {
            name: CatalogItem.objects.create(
                organization=self.org,
                name=name,
                unit=unit,
                unit_price=Decimal(price),
                recurring=recurring,
                service_kind=kind,
            )
            for name, unit, price, recurring, kind in (
                ("Social media management", "month", "1800.00", True, "social_media"),
                ("Brand identity", "project", "3000.00", False, "branding"),
                ("Brand strategy workshop", "project", "900.00", False, "brand_strategy"),
                ("Content shoot day", "day", "460.00", False, "social_media"),
                ("Extra reel", "item", "150.00", False, "social_media"),
            )
        }

        def issued(invoice, days_ago, *, due_days=14):
            """Send it, then move its dates back as if it went out ``days_ago`` days ago."""
            invoice = bill.send_invoice(invoice, owner, notify=False)
            Invoice.objects.filter(pk=invoice.pk).update(
                issue_date=today - td(days=days_ago), due_date=today - td(days=days_ago - due_days)
            )
            invoice.refresh_from_db()
            return bill.recalculate(invoice, today=today)

        def line(name, qty=1, **kw):
            item = catalog[name]
            return {"description": kw.pop("description", item.name), "quantity": qty, "unit_price": item.unit_price,
                    "service_kind": item.service_kind, "catalog_item": item, **kw}  # fmt: skip

        # Casa Maya: retainer invoices paid, current one with a receipt to confirm, extras overdue, next month drafted.
        october = issued(bill.create_invoice(casa, owner, purpose="retainer", title="Social media, October",
                                             lines=[line("Social media management")]), 50)  # fmt: skip
        bill.record_payment(
            october, owner, amount=october.balance, received_on=today - td(days=40), reference="TRF-10422"
        )
        november = issued(bill.create_invoice(casa, owner, purpose="retainer", title="Social media, November",
                                              lines=[line("Social media management")]), 10)  # fmt: skip
        bill.mark_viewed(november, ana)
        bill.submit_payment_proof(
            november, ana, amount=november.balance, upload=self._photo("transfer-receipt.png", (240, 238, 234), (40, 40, 40)),
            reference="TRF-11873",
        )  # fmt: skip
        extras = issued(bill.create_invoice(casa, owner, purpose="extra", title="October extras",
                                            lines=[line("Content shoot day"), line("Extra reel", 2)]), 30)  # fmt: skip
        bill.record_payment(extras, owner, amount=Decimal("400.00"), received_on=today - td(days=12), reference="Cash at the shoot",
                            method=Payment.Method.CASH)  # fmt: skip
        credit = bill.create_credit_note(extras, owner, amount=Decimal("168.75"), reason="One extra reel not delivered")
        bill.send_invoice(credit, owner, notify=False)
        bill.create_invoice(
            casa, None, purpose="retainer", title="Social media, December", lines=[line("Social media management")]
        )

        # Luma: brand refresh signed and deposit paid; a new proposal waiting; a USD invoice (pegged 2:1).
        brand = bill.create_proposal(
            luma, owner, title="Luma brand refresh", services=["branding"], deposit_percent=Decimal("50"), start_onboarding=False,
            lines=[{"description": "Brand identity", "unit_price": Decimal("3000.00"), "catalog_item": catalog["Brand identity"]}],
        )  # fmt: skip
        bill.send_proposal(brand, owner, notify=False)
        accepted = bill.accept_proposal(brand, lia, name="Lia Chen", agreed=True)
        bill.sign_contract(accepted["contract"], lia, name="Lia Chen", agreed=True, ip="203.0.113.24")
        if accepted["invoice"]:
            bill.record_payment(accepted["invoice"], owner, amount=accepted["invoice"].balance, reference="TRF-20811")
        offer = bill.create_proposal(
            luma, owner, title="Luma social media 2027", services=["social_media"], deposit_percent=Decimal("0"),
            start_onboarding=False,
            lines=[{"description": "Brand strategy workshop", "unit_price": Decimal("900.00"),
                    "catalog_item": catalog["Brand strategy workshop"]},
                   {"description": "Social media management", "unit_price": Decimal("1800.00"), "recurring": True,
                    "catalog_item": catalog["Social media management"]}],
        )  # fmt: skip
        bill.send_proposal(offer, owner, notify=False)
        usd = bill.create_invoice(luma, owner, purpose="extra", title="Influencer kit shipping", currency="USD",
                                  exchange_rate=Decimal("2.000000"), lines=[{"description": "Courier, Belize City to Miami",
                                                                       "unit_price": Decimal("85.00"), "tax_rate": 0}])  # fmt: skip
        issued(usd, 5)

        # Vista Realty (onboarding): the contract is out for signature and the deposit invoice is sent.
        listing = bill.create_proposal(
            vista, owner, title="Vista Realty listings content", services=["social_media"], deposit_percent=Decimal("50"),
            start_onboarding=False,
            lines=[{"description": "Listing photo and video package", "unit_price": Decimal("2400.00")}],
        )  # fmt: skip
        bill.send_proposal(listing, owner, notify=False)
        listing.status = listing.Status.ACCEPTED
        listing.accepted_name, listing.decided_by, listing.decided_at = "Marco Ruiz", marco, self.now - td(days=3)
        listing.save()
        contract = bill.create_contract(vista, owner, proposal=listing)
        bill.send_contract(contract, owner, notify=False)
        deposit = bill.create_invoice(vista, owner, purpose="deposit", title=f"Deposit for {listing.number}", proposal=listing,
                                      lines=[{"description": "Deposit (50%) for listings content", "unit_price": Decimal("1200.00")}])  # fmt: skip
        issued(deposit, 2)

        # Messages tied to items.
        thread = talk.start(casa, ana, subject="", body="Hi! Can we split the November payment in two this time?",
                            from_client=True, about="invoice", about_id=november.id)  # fmt: skip
        talk.reply(
            thread,
            owner,
            "Of course. Pay half now and half by the 15th; I've noted it on the invoice.",
            from_client=False,
        )
        talk.start(luma, lia, subject="Night serum launch date", body="Could we move the launch posts to the 12th?",
                   from_client=True)  # fmt: skip

        # Languages: Marco reads the portal in Spanish; Vista's portal defaults to Spanish.
        ClientProfile.objects.filter(workspace=vista).update(portal_language="es")
        set_language(marco, "es")


def seed_demo(now=None) -> DemoResult:
    from apps.reviews.services import bypass

    builder = _Builder(now)
    # Demo posts are created directly in their final states (scheduled, published),
    # which the review checks rightly refuse for real content.
    with bypass():
        result = builder.build()
    # Phase 4's walk-through runs with the review rules on, like real work.
    builder.content_operations()
    builder.billing()
    return result
