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


def seed_demo(now=None) -> DemoResult:
    return _Builder(now).build()
