"""Bring templates and Python-built markup onto the design tokens.

    python manage.py apply_design_tokens            # rewrite files, print a summary
    python manage.py apply_design_tokens --check    # exit 1 if anything would change
    python manage.py apply_design_tokens --report /tmp/tokens.txt

Run it after every upstream merge. See apps/design_system/codemod.py.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.design_system.codemod import rewrite


def target_files(base: Path) -> list[Path]:
    """Templates (except emails, which use inline styles) and the Python that builds class strings."""
    files = [p for p in (base / "templates").rglob("*.html") if "email" not in p.parts]
    for p in (base / "apps").rglob("*"):
        if p.suffix not in (".html", ".py") or not p.is_file():
            continue
        parts = set(p.parts)
        if "email" in parts or "migrations" in parts or "tests" in parts or "design_system" in parts:
            continue
        if p.suffix == ".py" and (p.name.startswith("test_") or p.name == "tests.py"):
            continue
        if p.suffix == ".html" and "templates" not in parts:
            continue
        files.append(p)
    return sorted(files)


class Command(BaseCommand):
    help = "Rewrite Tailwind palette, type-size, radius and shadow classes onto the studio design tokens."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Change nothing; exit 1 if a file would change.")
        parser.add_argument("--report", help="Write every change, with review notes, to this file.")

    def handle(self, *args, check=False, report=None, **options):
        base = Path(settings.BASE_DIR)
        changed_files = 0
        total = 0
        review = 0
        lines = []
        for path in target_files(base):
            original = path.read_text(encoding="utf-8")
            result = rewrite(original, str(path.relative_to(base)))
            if result.text == original:
                continue
            changed_files += 1
            total += len(result.changes)
            for c in result.changes:
                flag = "REVIEW " if c.review else ""
                review += bool(c.review)
                lines.append(
                    f"{flag}{c.path}:{c.line}\n  - {c.before}\n  + {c.after}"
                    + (f"\n  ! {c.review}" if c.review else "")
                )
            if not check:
                path.write_text(result.text, encoding="utf-8")

        if report:
            Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
        verb = "would change" if check else "changed"
        self.stdout.write(f"{changed_files} files {verb}, {total} lines, {review} marked for review.")
        if check and changed_files:
            raise CommandError("Templates are not on the design tokens. Run: python manage.py apply_design_tokens")
