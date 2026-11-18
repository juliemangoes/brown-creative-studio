"""Guards: templates, CSS and markup-building Python stay on the design tokens.

These fail when an upstream merge (or new code) brings back a raw Tailwind
palette class, BrightBean's orange, a one-off colour, or a size, radius or shadow
outside the scales. The usual fix is:

    python manage.py apply_design_tokens
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

from apps.design_system.codemod import UI_HEX, rewrite
from apps.design_system.management.commands.apply_design_tokens import target_files

BASE = Path(settings.BASE_DIR)

# Logos and platform chips keep their owners' brand colours.
PLATFORM_ICON_FILES = {"_platform_icon.html"}
PLATFORM_HEX = {
    "1877F2",  # Facebook
    "0A66C2",  # LinkedIn
    "E60023",  # Pinterest
    "BD081C",  # Pinterest
    "0085FF",  # Bluesky
    "FE2C55",  # TikTok
    "25F4EE",  # TikTok
    "3897F0",  # Instagram
    "F58529",  # Instagram
    "DD2A7B",  # Instagram
    "8134AF",  # Instagram
    "E4405F",  # Instagram
    "6364FF",  # Mastodon
    "FF0000",  # YouTube
    "4285F4",  # Google
    "34A853",  # Google
    "FBBC05",  # Google
    "EA4335",  # Google
}
# BrightBean's brand colours: orange (and its tints) and the bean green.
BRIGHTBEAN_HEX = {
    "F97316",
    "EA580C",
    "FB923C",
    "FDBA74",
    "FED7AA",
    "FFEDD5",
    "FFF7ED",
    "C2410C",
    "9A3412",
    "7C2D12",
    "94A43F",
    "93A240",
    "7B8A33",
    "849139",
    "EFF2DC",
    "DDE4B9",
}
HEX_RE = re.compile(r"#([0-9A-Fa-f]{6})\b")


def _rel(path: Path) -> str:
    return str(path.relative_to(BASE))


def _page_templates():
    for root in (BASE / "templates", BASE / "apps"):
        for path in root.rglob("*.html"):
            if "node_modules" in path.parts or ("apps" in path.parts and "templates" not in path.parts):
                continue
            yield path


def test_templates_and_python_markup_are_on_the_tokens():
    stale = []
    for path in target_files(BASE):
        text = path.read_text(encoding="utf-8")
        result = rewrite(text, _rel(path))
        if result.text != text:
            stale.append(f"{_rel(path)}:{result.changes[0].line}  {result.changes[0].before[:100]}")
    assert not stale, "Not on the design tokens (run `python manage.py apply_design_tokens`):\n" + "\n".join(stale)


def test_no_palette_hex_in_page_templates():
    found = []
    for path in _page_templates():
        if "email" in path.parts or path.name in PLATFORM_ICON_FILES:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in HEX_RE.finditer(line):
                if m[1].upper() in UI_HEX:
                    found.append(f"{_rel(path)}:{lineno}  #{m[1]}")
    assert not found, "Tailwind or BrightBean palette colours in templates; use a token:\n" + "\n".join(found)


def _brand_sources():
    yield from _page_templates()
    yield from (BASE / "theme" / "static_src" / "src").glob("*.css")
    yield from (p for p in (BASE / "static" / "js").glob("*.js"))
    for path in (BASE / "apps").rglob("*.py"):
        if "migrations" in path.parts or "tests" in path.parts or path.name.startswith("test"):
            continue
        if path.parent.name == "design_system" or "design_system" in path.parts:
            continue
        yield path


def test_no_brightbean_orange_or_green_left():
    found = []
    for path in _brand_sources():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "249,115,22" in line.replace(" ", ""):
                found.append(f"{_rel(path)}:{lineno}  rgb(249, 115, 22)")
            for m in HEX_RE.finditer(line):
                if m[1].upper() in BRIGHTBEAN_HEX:
                    found.append(f"{_rel(path)}:{lineno}  #{m[1]}")
    assert not found, "BrightBean brand colours remain:\n" + "\n".join(found)


def test_arbitrary_colour_classes_are_platform_colours_only():
    pattern = re.compile(
        r"(?<![\w-])(?:[\w-]+:)*(?:bg|text|border|ring|fill|stroke|from|to|via)-\[#([0-9A-Fa-f]{3,6})\]"
    )
    found = []
    for path in _page_templates():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                if m[1].upper() not in PLATFORM_HEX:
                    found.append(f"{_rel(path)}:{lineno}  {m.group(0)}")
    assert not found, "One-off colour classes; use a token:\n" + "\n".join(found)


def _defined_custom_properties() -> set[str]:
    defined: set[str] = set()
    sources = list((BASE / "theme" / "static_src" / "src").glob("*.css")) + list(_page_templates())
    for path in sources:
        defined.update(re.findall(r"(--[A-Za-z0-9-]+)\s*:", path.read_text(encoding="utf-8")))
    return defined


def test_every_css_variable_a_template_uses_is_defined():
    defined = _defined_custom_properties()
    missing = []
    for path in _page_templates():
        if "email" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in re.findall(r"var\((--[A-Za-z0-9-]+)", line):
                if name not in defined and not name.startswith("--tw-"):
                    missing.append(f"{_rel(path)}:{lineno}  {name}")
    assert not missing, "CSS variables used but never defined:\n" + "\n".join(missing)


@pytest.mark.parametrize("name", ["styles.css", "components.css"])
def test_stylesheets_use_tokens_not_palette_hex(name):
    """Hex colours in the stylesheets live only in the token definitions and font/chevron data."""
    text = (BASE / "theme" / "static_src" / "src" / name).read_text(encoding="utf-8")
    found = [m[1] for m in HEX_RE.finditer(text) if m[1].upper() in UI_HEX]
    assert not found, f"{name} uses Tailwind palette colours: {sorted(set(found))}"
