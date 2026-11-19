"""Load the demo studio: three clients, staff and client users, posts in every state.

    python manage.py seed_demo            # refuses if the demo already exists
    python manage.py seed_demo --reset    # remove the previous demo first

Runs only with DEBUG on, unless --force is given (for a staging server).
Passwords are generated on every run and printed once.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.studio.demo import DEMO_ORG_NAME, demo_exists, remove_demo, seed_demo


class Command(BaseCommand):
    help = "Create demo clients (a restaurant, a skincare brand, a real estate company) with users and posts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true", help="Delete the existing demo studio and demo users first."
        )
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off (never on production).")

    def handle(self, *args, reset=False, force=False, **options):
        if not settings.DEBUG and not force:
            raise CommandError(
                "seed_demo only runs with DEBUG on. Use --force on a staging server, never on production."
            )
        if demo_exists():
            if not reset:
                raise CommandError(f'"{DEMO_ORG_NAME}" already exists. Run with --reset to replace it.')
            remove_demo()
            self.stdout.write("Removed the previous demo.")

        result = seed_demo()
        out = self.stdout
        out.write(self.style.SUCCESS(f'Created "{result.organization.name}" with 3 clients.'))
        out.write("")
        out.write("Sign in at /accounts/login/ with these generated passwords (shown once, not saved anywhere):")
        width = max(len(role) for role, _, _ in result.logins)
        for role, email, password in result.logins:
            out.write(f"  {role.ljust(width)}  {email}  {password}")
        out.write("")
        out.write("Client portal links (single use, expire like any magic link):")
        for client, email, url in result.portal_links:
            out.write(f"  {client}: {email}\n    {url}")
        out.write("")
        out.write(
            self.style.WARNING(
                "Demo social accounts are placeholders with no tokens. Do not run the publishing worker "
                "against them: due posts would fail."
            )
        )
