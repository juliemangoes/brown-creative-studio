"""The colour tokens in styles.css meet WCAG 2.2 AA in both themes, and the two dark blocks agree."""

import re
from pathlib import Path

import pytest
from django.conf import settings

STYLES = (Path(settings.BASE_DIR) / "theme" / "static_src" / "src" / "styles.css").read_text(encoding="utf-8")

TEXT_TOKENS = ("text-primary", "text-secondary", "accent", "success", "warning", "danger", "info")
BACKGROUNDS = ("canvas", "surface", "sunken")


def _block(selector_pattern: str) -> dict[str, str]:
    """The custom properties declared in the first rule whose selector matches."""
    m = re.search(selector_pattern + r"\s*\{(?P<body>[^}]*)\}", STYLES)
    assert m, f"no block for {selector_pattern}"
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9A-Fa-f]{6})\s*;", m["body"]))


LIGHT = _block(r":root(?=\s*\{\s*color-scheme:\s*light;)")
DARK_MEDIA = _block(r':root:not\(\[data-theme="light"\]\)')
DARK_CHOSEN = _block(r':root\[data-theme="dark"\]')


def _luminance(hex_value: str) -> float:
    channels = [int(hex_value[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_spec_values_are_exact():
    """The anchor values of the modern palette (docs/fork/design-system.md); the brand brown is unchanged."""
    assert LIGHT["canvas"] == "#F5F4F2" and DARK_CHOSEN["canvas"] == "#0F0D0C"
    assert LIGHT["surface"] == "#FFFFFF" and DARK_CHOSEN["surface"] == "#181614"
    assert LIGHT["accent"] == "#6F4A32" and DARK_CHOSEN["accent"] == "#D2A77F"
    assert LIGHT["text-primary"] == "#1C1714" and DARK_CHOSEN["text-primary"] == "#F3F1EE"
    assert LIGHT["control-border"] == "#8A847D" and DARK_CHOSEN["control-border"] == "#77706A"


def test_dark_theme_is_identical_whether_chosen_or_from_the_system():
    assert DARK_MEDIA == DARK_CHOSEN


def test_both_themes_define_the_same_tokens():
    assert set(LIGHT) == set(DARK_CHOSEN)


@pytest.mark.parametrize("theme", [LIGHT, DARK_CHOSEN], ids=["light", "dark"])
@pytest.mark.parametrize("text", TEXT_TOKENS)
@pytest.mark.parametrize("background", BACKGROUNDS)
def test_text_meets_aa_on_every_background(theme, text, background):
    assert contrast(theme[text], theme[background]) >= 4.5


@pytest.mark.parametrize("theme", [LIGHT, DARK_CHOSEN], ids=["light", "dark"])
@pytest.mark.parametrize(
    ("text", "background"),
    [
        ("on-accent", "accent"),
        ("on-accent", "accent-hover"),
        ("on-accent", "danger"),
        ("on-accent", "success"),
        ("accent", "accent-soft"),
        ("success", "success-soft"),
        ("warning", "warning-soft"),
        ("danger", "danger-soft"),
        ("info", "info-soft"),
        ("text-primary", "accent-soft"),
    ],
)
def test_filled_pairs_meet_aa(theme, text, background):
    assert contrast(theme[text], theme[background]) >= 4.5


@pytest.mark.parametrize("theme", [LIGHT, DARK_CHOSEN], ids=["light", "dark"])
@pytest.mark.parametrize("ui", ["control-border", "accent"])
@pytest.mark.parametrize("background", BACKGROUNDS)
def test_controls_meet_non_text_contrast(theme, ui, background):
    """WCAG 1.4.11: input borders and the focus ring need 3:1 against what is behind them."""
    assert contrast(theme[ui], theme[background]) >= 3.0
