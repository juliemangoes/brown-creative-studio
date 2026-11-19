"""Rewrite BrightBean's Tailwind classes onto Brown Creative Studio's design tokens.

BrightBean's templates use Tailwind's palette directly (``bg-orange-500``,
``text-stone-600``…), its default type scale (``text-sm``), radii
(``rounded-xl``) and shadows (``shadow-sm``). This fork switches all of those
off in ``theme/static_src/src/styles.css`` and keeps only semantic utilities
(``bg-accent``, ``text-ink-muted``, ``text-14``, ``rounded-panel``…).

The rewrite is mechanical and deterministic, so it lives here as code rather
than as a one-off edit: after merging upstream, run

    python manage.py apply_design_tokens

to bring any new or changed upstream template onto the tokens, then review
the report it prints. ``apps/design_system/tests/test_guards.py`` fails while
anything is left.

What it does not decide: ``text-white`` depends on the background behind it,
which is not always on the same line. Those calls are made from nearby
classes and listed as "review" in the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NEUTRALS = {"stone", "gray", "neutral", "zinc", "slate"}
FAMILIES = {
    "orange": "accent",
    "indigo": "accent",
    "purple": "accent",
    "violet": "accent",
    "pink": "accent",
    "fuchsia": "accent",
    "amber": "warning",
    "yellow": "warning",
    "red": "danger",
    "rose": "danger",
    "green": "success",
    "emerald": "success",
    "lime": "success",
    "teal": "success",
    "blue": "info",
    "sky": "info",
    "cyan": "info",
}
PALETTE = NEUTRALS | set(FAMILIES)

# Variants such as hover:, md:, group-hover:, [&>svg]:, data-[state=open]:
_PRE = r"(?P<pre>(?:[\w\-\[\]&>*@.=()'#%,]+:)*)"
_START = r"(?<![\w\-/\[.$])"
_END = r"(?![\w\-\[])"

_COLOR_UTILS = (
    "ring-offset",
    "border-x",
    "border-y",
    "border-t",
    "border-b",
    "border-l",
    "border-r",
    "border-s",
    "border-e",
    "bg",
    "text",
    "border",
    "ring",
    "divide",
    "outline",
    "placeholder",
    "from",
    "via",
    "to",
    "fill",
    "stroke",
    "accent",
    "caret",
    "decoration",
    "shadow",
)
_COLOR_NAMES = sorted(PALETTE | {"white", "black"}, key=len, reverse=True)

COLOR_RE = re.compile(
    _START
    + _PRE
    + r"(?P<imp>!?)(?P<util>"
    + "|".join(_COLOR_UTILS)
    + r")-(?P<color>"
    + "|".join(_COLOR_NAMES)
    + r")(?:-(?P<shade>50|[1-9]00|950))?(?:/(?P<op>\d{1,3}))?"
    + _END
)
RADIUS_RE = re.compile(
    _START + _PRE + r"(?P<imp>!?)rounded(?P<side>-(?:tl|tr|bl|br|ss|se|es|ee|t|b|l|r|s|e))?"
    r"(?:-(?P<size>none|xs|sm|md|lg|xl|2xl|3xl|full|\[[^\]\s]+\]))?" + _END
)
SHADOW_RE = re.compile(
    _START + _PRE + r"(?P<imp>!?)shadow(?:-(?P<size>xs|sm|md|lg|xl|2xl|inner|none|\[[^\]\s]+\]))?" + _END
)
TEXT_SIZE_RE = re.compile(
    _START
    + _PRE
    + r"(?P<imp>!?)text-(?P<size>xs|sm|base|lg|xl|2xl|3xl|4xl|5xl|6xl|7xl|\[(?P<num>[0-9.]+)(?P<unit>px|rem)\])"
    r"(?P<lh>/[\w.\[\]-]+)?" + _END
)
DURATION_RE = re.compile(_START + _PRE + r"(?P<imp>!?)duration-(?P<ms>\d+)" + _END)
BLUR_RE = re.compile(_START + _PRE + r"(?P<imp>!?)backdrop-(?:blur|saturate)(?:-[\w\[\].]+)?" + _END)
# Hover "lifts" (raise and cast a shadow) are not part of this design; hover is a colour change.
LIFT_RE = re.compile(
    r"(?<![\w\-/\[.$])(?:group-)?hover:(?:-?translate-y-[\w.\[\]]+|shadow-(?:menu|dialog))(?![\w\-\[])"
)

_TW_SIZES_PX = {
    "xs": 12,
    "sm": 14,
    "base": 16,
    "lg": 18,
    "xl": 20,
    "2xl": 24,
    "3xl": 30,
    "4xl": 36,
    "5xl": 48,
    "6xl": 60,
    "7xl": 72,
}
_HOVER_VARIANTS = ("hover:", "focus:", "active:", "focus-within:", "focus-visible:", "group-hover:", "peer-hover:")

# Cues for what sits behind text-white, looked for near the token.
# Platform chips (pi-<platform> sets the brand colour) and brand-coloured fills keep white text.
_CUE_PLATFORM = re.compile(r"bg-\[#|\bpi-[\w{]|linear-gradient\(\s*\d+deg,\s*#")
_CUE_SCRIM = re.compile(r"\b(?:bg|from|via)-(?:black|scrim)\b|\b(?:bg|from)-black/|bg-scrim/")
_CUE_SOLID = re.compile(
    r"\bbg-(?:orange|red|rose|green|emerald|blue|sky|amber|yellow|indigo|purple|teal|lime|cyan|violet|pink)-(?:[5-9]00)\b"
    r"|\bbg-(?:accent|danger|success|warning|info)\b(?![\w-])"
    r"|var\(--(?:primary|brand-[5-9]00|error-[5-7]00|success-[5-7]00|accent|danger)[\w-]*\)"
    r"|\bbtn-(?:primary-hover|primary-lift|error-darker|brand|danger-soft)\b|style=\"background:\s*#"
)
_CUE_INK = re.compile(r"\bbg-(?:stone|gray|neutral|zinc|slate)-(?:[5-9]00|950)\b|\bbg-ink(?:-muted)?\b")


@dataclass
class Change:
    path: str
    line: int
    before: str
    after: str
    review: str = ""


@dataclass
class Result:
    text: str
    changes: list[Change] = field(default_factory=list)


def _px(num: str, unit: str) -> float:
    return float(num) * (16 if unit == "rem" else 1)


def nearest_size(px: float) -> int:
    """The step of the 12/13/14/16/20/24/32/44 scale closest to ``px`` (ties go down)."""
    if px <= 12.5:
        return 12
    if px < 13.5:
        return 13
    if px <= 15:
        return 14
    if px <= 18:
        return 16
    if px <= 22:
        return 20
    if px <= 28:
        return 24
    if px <= 38:
        return 32
    return 44


def _family_color(util: str, family: str, shade: int, op: str | None, hover: bool) -> str | None:
    base = "border" if util.startswith("border") else util
    with_op = f"/{op}" if op else ""
    if base in ("text", "fill", "stroke", "caret", "decoration"):
        return ("on-accent" if shade <= 200 else family) + with_op
    if base == "placeholder":
        return "ink-muted"
    if base == "accent":
        return "accent"
    if base == "ring-offset":
        return "surface"
    if base in ("bg", "from", "via", "to"):
        if shade <= 200:
            return f"{family}-soft{with_op}"
        if family == "accent" and (shade >= 700 or (hover and shade >= 500)):
            return f"accent-hover{with_op}"
        return family + with_op
    # border, divide, ring, outline
    if shade <= 200:
        return f"accent-line{with_op}" if family == "accent" else f"{family}/30"
    return family + with_op


def _neutral_color(util: str, shade: int, op: str | None, hover: bool) -> str | None:
    base = "border" if util.startswith("border") else util
    with_op = f"/{op}" if op else ""
    if base in ("text", "fill", "stroke"):
        if shade >= 700:
            return "ink" + with_op
        if shade >= 300:
            return "ink-muted" + with_op
        return "canvas" + with_op
    if base == "placeholder":
        return "ink-muted"
    if base == "caret" or base == "accent":
        return "accent"
    if base == "decoration":
        return "control" if shade >= 300 else "hairline"
    if base == "ring-offset":
        return "surface"
    if base in ("bg", "from", "via", "to"):
        if shade == 50:
            return ("sunken" if hover else "canvas") + with_op
        if shade == 100:
            return "sunken" + with_op
        if shade == 200:
            return "hairline" + with_op
        if shade <= 400:
            return "control" + with_op
        if shade <= 600:
            return "ink-muted" + with_op
        return "ink" + with_op
    # border, divide, ring, outline
    if shade <= 200:
        return "hairline" + with_op
    if shade <= 500:
        return "control" + with_op
    return "ink-muted" + with_op


def _white_text(context: str) -> tuple[str, str]:
    """What text-white becomes, given the classes around it; second item is a review note."""
    if _CUE_PLATFORM.search(context):
        return "on-scrim", ""
    if _CUE_SCRIM.search(context):
        return "on-scrim", ""
    if _CUE_SOLID.search(context):
        return "on-accent", ""
    if _CUE_INK.search(context):
        return "canvas", ""
    return "on-accent", "text-white with no background nearby: assumed on an accent fill"


def rewrite(text: str, path: str = "") -> Result:
    """Apply every mapping to ``text`` and return the new text plus a change log."""
    changes: list[Change] = []
    lines = text.split("\n")
    out_lines = []
    for index, line in enumerate(lines):
        context = "\n".join(lines[max(0, index - 3) : index + 4])
        new = _rewrite_line(line, context, path, index + 1, changes)
        out_lines.append(new)
    text = "\n".join(out_lines)
    hexed = rewrite_rgb(rewrite_css_hex(text))
    if hexed != text:
        changes.append(Change(path, 0, "hex colours in CSS", "token variables"))
        text = hexed
    text, pill_changes = _rewrite_pills(text, path)
    changes.extend(pill_changes)
    return Result(text=text, changes=changes)


_CLASS_SPAN_RE = re.compile(r'(?:x-bind:class|:class|class)="[^"]*"|class=\'[^\']*\'')


def _in_class_attr(line: str, pos: int) -> bool:
    """Whether ``pos`` falls inside a class attribute (static or Alpine-bound) on ``line``."""
    return any(m.start() < pos < m.end() for m in _CLASS_SPAN_RE.finditer(line))


def _rewrite_line(line: str, context: str, path: str, lineno: int, changes: list[Change]) -> str:
    original = line
    notes: list[str] = []
    line_now = line

    def color(m: re.Match) -> str:
        pre, imp, util, color_name, shade_s, op = (
            m["pre"],
            m["imp"],
            m["util"],
            m["color"],
            m["shade"],
            m["op"],
        )
        hover = any(v in pre for v in _HOVER_VARIANTS)
        base = "border" if util.startswith("border") else util
        if util == "shadow":
            return ""  # coloured shadows go with the shadows
        if color_name == "white":
            if base in ("text", "fill", "stroke"):
                token, note = _white_text(context)
                if note:
                    notes.append(f"{m.group(0)} -> {note}")
                return f"{pre}{imp}{util}-{token}" + (f"/{op}" if op else "")
            if base == "placeholder":
                return f"{pre}{imp}{util}-ink-muted"
            if base == "ring-offset":
                return f"{pre}{imp}{util}-surface"
            return f"{pre}{imp}{util}-surface" + (f"/{op}" if op else "")
        if color_name == "black":
            if base in ("bg", "from", "via", "to"):
                return f"{pre}{imp}{util}-scrim" + (f"/{op}" if op else "")
            return f"{pre}{imp}{util}-ink" + (f"/{op}" if op else "")
        if shade_s is None:
            return m.group(0)
        shade = int(shade_s)
        mapped = (
            _neutral_color(util, shade, op, hover)
            if color_name in NEUTRALS
            else _family_color(util, FAMILIES[color_name], shade, op, hover)
        )
        return f"{pre}{imp}{util}-{mapped}" if mapped else ""

    def radius(m: re.Match) -> str:
        pre, imp, side, size = m["pre"], m["imp"], m["side"] or "", m["size"]
        if size in ("none", "full"):
            return m.group(0)
        if not size and not side and not _in_class_attr(line_now, m.start()):
            return m.group(0)  # the word "rounded" in prose
        if size and size.startswith("["):
            value = size[1:-1]
            if value.endswith("%") or value in ("9999px", "100px"):
                return f"{pre}{imp}rounded{side}-full"
            num = re.match(r"([0-9.]+)(px|rem)?$", value)
            if not num:
                notes.append(f"{m.group(0)} -> unrecognised radius, set to control")
                return f"{pre}{imp}rounded{side}-control"
            px = _px(num[1], num[2] or "px")
            return f"{pre}{imp}rounded{side}-" + ("control" if px <= 8 else "panel")
        name = "panel" if size in ("xl", "2xl", "3xl") else "control"
        return f"{pre}{imp}rounded{side}-{name}"

    def shadow(m: re.Match) -> str:
        pre, imp, size = m["pre"], m["imp"], m["size"]
        if size == "none":
            return m.group(0)
        if not size and not _in_class_attr(line_now, m.start()):
            return m.group(0)  # the word "shadow" in prose
        if size in ("lg", "xl"):
            return f"{pre}{imp}shadow-menu"
        if size == "2xl":
            return f"{pre}{imp}shadow-dialog"
        return ""  # xs, sm, md, inner, bare and arbitrary shadows: separate with hairlines instead

    def text_size(m: re.Match) -> str:
        pre, imp, size, lh = m["pre"], m["imp"], m["size"], m["lh"] or ""
        px = _px(m["num"], m["unit"]) if m["num"] else _TW_SIZES_PX[size]
        return f"{pre}{imp}text-{nearest_size(px)}{lh}"

    def duration(m: re.Match) -> str:
        ms = int(m["ms"])
        clamped = min(max(ms, 120), 200)
        return f"{m['pre']}{m['imp']}duration-{clamped}"

    line = COLOR_RE.sub(color, line)
    line_now = line
    line = RADIUS_RE.sub(radius, line)
    line_now = line
    line = SHADOW_RE.sub(shadow, line)
    line = TEXT_SIZE_RE.sub(text_size, line)
    line = DURATION_RE.sub(duration, line)
    line = BLUR_RE.sub("", line)
    line = LIFT_RE.sub("", line)
    if line != original:
        # Removed tokens leave doubled spaces between classes; indentation is untouched.
        line = re.sub(r"(?<=\S) {2,}(?=\S)", " ", line)
        line = re.sub(r'(class=")[ \t]+', r"\1", line)
        line = re.sub(r'(class="[^"\n]*?)[ \t]+"', r'\1"', line)
        changes.append(Change(path, lineno, original.strip(), line.strip(), "; ".join(notes)))
    return line


_CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')
_PILL_PADDING_RE = re.compile(r"(?<![\w-])px-(?:2|2\.5|3|3\.5|4|5|6|8)(?![\w-])")


def _rewrite_pills(text: str, path: str) -> tuple[str, list[Change]]:
    """Pill-shaped buttons and labels (rounded-full with horizontal padding) become 6 px controls.

    Avatars, dots and toggle tracks keep rounded-full: they have no px- padding.
    """
    changes: list[Change] = []

    def fix(m: re.Match) -> str:
        value = m.group(1)
        if "rounded-full" in value and _PILL_PADDING_RE.search(value):
            new_value = re.sub(r"(?<![\w-])((?:[\w-]+:)*)rounded-full(?![\w-])", r"\1rounded-control", value)
            if new_value != value:
                lineno = text.count("\n", 0, m.start()) + 1
                changes.append(Change(path, lineno, "rounded-full (pill)", "rounded-control"))
            return f'class="{new_value}"'
        return m.group(0)

    return _CLASS_ATTR_RE.sub(fix, text), changes


# ── Hex colours in style="…" attributes and <style> blocks ────────────────────
# Tailwind's palette (and BrightBean's orange and bean green) also appears as
# literal hex in CSS. Each known value maps to a token by family and shade, and
# the CSS property decides which token: text, fill, background or border.
# Platform brand colours (logos, platform chips) are not in this table and are
# left alone.

_HEX_SHADES: dict[str, tuple[str, int]] = {}


def _register(family: str, shades: dict[int, str]) -> None:
    for shade, hex_value in shades.items():
        _HEX_SHADES[hex_value.upper()] = (family, shade)


_register(
    "neutral",
    {
        50: "FAFAF9",
        100: "F5F5F4",
        200: "E7E5E4",
        300: "D6D3D1",
        400: "A8A29E",
        500: "78716C",
        600: "57534E",
        700: "44403C",
        800: "292524",
        900: "1C1917",
        950: "0C0A09",
    },
)  # stone
_register(
    "neutral",
    {
        50: "F9FAFB",
        100: "F3F4F6",
        200: "E5E7EB",
        300: "D1D5DB",
        400: "9CA3AF",
        500: "6B7280",
        600: "4B5563",
        700: "374151",
        800: "1F2937",
        900: "111827",
    },
)  # gray
# BrightBean's own near-neutrals
_register(
    "neutral",
    {
        50: "FAFAF7",
        51: "F7F7F4",
        52: "FFFFF9",
        100: "F3F4F1",
        101: "EEEEEC",
        200: "EAE8E5",
        900: "21201C",
        901: "1F2421",
        902: "213130",
    },
)
_register(
    "accent",
    {
        50: "FFF7ED",
        100: "FFEDD5",
        200: "FED7AA",
        300: "FDBA74",
        301: "FFBB8A",
        400: "FB923C",
        500: "F97316",
        600: "EA580C",
        700: "C2410C",
        800: "9A3412",
        900: "7C2D12",
    },
)  # orange
_register("accent", {50: "F5F3FF", 200: "DDD6FE", 500: "8B5CF6", 600: "7C3AED", 700: "6D28D9"})  # violet
_register("accent", {50: "EEF2FF", 100: "E0E7FF", 200: "C7D2FE", 500: "6366F1", 600: "4F46E5", 700: "4338CA"})  # indigo
_register("accent", {500: "EC4899"})  # pink
_register(
    "accent",
    {
        100: "EFF2DC",
        101: "EEF5EB",
        102: "D9F1D1",
        200: "DDE4B9",
        500: "94A43F",
        501: "93A240",
        600: "7B8A33",
        601: "849139",
    },
)  # BrightBean bean green
_register(
    "warning",
    {
        50: "FFFBEB",
        100: "FEF3C7",
        200: "FDE68A",
        201: "FFD889",
        300: "FCD34D",
        400: "FBBF24",
        500: "F59E0B",
        600: "D97706",
        700: "B45309",
        800: "92400E",
        900: "78350F",
    },
)  # amber
_register(
    "warning",
    {
        50: "FEFCE8",
        100: "FEF9C3",
        200: "FEF08A",
        300: "FDE047",
        400: "FACC15",
        500: "EAB308",
        600: "CA8A04",
        700: "A16207",
        800: "854D0E",
    },
)  # yellow
_register(
    "danger",
    {
        50: "FEF2F2",
        100: "FEE2E2",
        200: "FECACA",
        300: "FCA5A5",
        400: "F87171",
        500: "EF4444",
        600: "DC2626",
        700: "B91C1C",
        800: "991B1B",
        900: "7F1D1D",
    },
)  # red
_register("danger", {50: "FFF1F2", 500: "F43F5E"})  # rose
_register(
    "success",
    {
        50: "F0FDF4",
        100: "DCFCE7",
        200: "BBF7D0",
        300: "86EFAC",
        400: "4ADE80",
        500: "22C55E",
        600: "16A34A",
        700: "15803D",
        800: "166534",
    },
)  # green
_register(
    "success",
    {
        50: "ECFDF5",
        100: "D1FAE5",
        200: "A7F3D0",
        300: "6EE7B7",
        400: "34D399",
        500: "10B981",
        600: "059669",
        700: "047857",
    },
)  # emerald
_register("success", {50: "F0FDFA", 100: "CCFBF1", 200: "99F6E4", 500: "14B8A6", 700: "0F766E"})  # teal
_register(
    "info", {50: "EFF6FF", 100: "DBEAFE", 200: "BFDBFE", 500: "3B82F6", 600: "2563EB", 700: "1D4ED8", 800: "1E40AF"}
)  # blue
_register("info", {50: "F0F9FF", 500: "0EA5E9"})  # sky

UI_HEX = frozenset(_HEX_SHADES)  # used by the guard test

_TEXT_PROPS = ("color", "fill", "stroke", "caret-color", "-webkit-text-fill-color", "text-decoration-color")
_BORDER_PROPS = ("border", "outline", "box-shadow", "column-rule")
_STYLE_ATTR_RE = re.compile(r'style="([^"]*)"')
_STYLE_BLOCK_RE = re.compile(r"(<style[^>]*>)(.*?)(</style>)", re.DOTALL | re.IGNORECASE)
# Not after "|" or a word character: "|default:'#fff'" is a Django filter, not CSS.
_DECL_RE = re.compile(r"(?<![|\w-])(?P<prop>-?[a-z][a-z-]*)\s*:\s*(?P<val>[^;{}\"]*)", re.IGNORECASE)
_HEX_RE = re.compile(r"#(?P<hex>[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b")
_KEYWORD_RE = re.compile(r"(?<![\w-])(white|black)(?![\w-])", re.IGNORECASE)


def _hex_token(prop: str, hex_value: str) -> str | None:
    hv = hex_value.upper()
    if len(hv) == 3:
        hv = "".join(ch * 2 for ch in hv)
    prop = prop.lower()
    is_text = prop in _TEXT_PROPS
    is_border = prop.startswith(_BORDER_PROPS)
    if hv == "FFFFFF":
        if is_text:
            return "var(--on-accent)"
        return "var(--surface)"
    if hv in ("000000", "010101"):
        return "var(--text-primary)" if (is_text or is_border) else "var(--scrim)"
    if hv not in _HEX_SHADES:
        return None
    family, shade = _HEX_SHADES[hv]
    shade = 50 if shade < 100 else shade // 100 * 100  # 51, 101… are near-duplicates of 50, 100…
    if family == "neutral":
        if is_text:
            return (
                "var(--text-primary)" if shade >= 700 else "var(--text-secondary)" if shade >= 300 else "var(--canvas)"
            )
        if is_border:
            return (
                "var(--hairline)"
                if shade <= 200
                else "var(--control-border)"
                if shade <= 500
                else "var(--text-secondary)"
            )
        return {50: "var(--canvas)", 100: "var(--sunken)", 200: "var(--hairline)"}.get(
            shade,
            "var(--control-border)"
            if shade <= 400
            else "var(--text-secondary)"
            if shade <= 600
            else "var(--text-primary)",
        )
    if is_text:
        return "var(--on-accent)" if shade <= 200 else f"var(--{family})"
    if is_border:
        if shade <= 300:
            return (
                "var(--accent-line)" if family == "accent" else f"color-mix(in srgb, var(--{family}) 30%, transparent)"
            )
        return f"var(--{family})"
    if shade <= 200:
        return f"var(--{family}-soft)"
    if family == "accent" and shade >= 700:
        return "var(--accent-hover)"
    return f"var(--{family})"


def _rewrite_declarations(css: str) -> str:
    def decl(m: re.Match) -> str:
        prop, val = m["prop"], m["val"]

        def hexsub(h: re.Match) -> str:
            token = _hex_token(prop, h["hex"])
            return token if token else h.group(0)

        lowered = prop.lower()
        if lowered == "font-size":
            size = re.fullmatch(r"\s*([0-9.]+)(px|rem)\s*(!important)?\s*", val)
            if size:
                step = nearest_size(_px(size[1], size[2]))
                return f"{prop}: {step / 16:g}rem{' !important' if size[3] else ''}"
        if lowered in (
            "border-radius",
            "border-top-left-radius",
            "border-top-right-radius",
            "border-bottom-left-radius",
            "border-bottom-right-radius",
        ):
            radius = re.fullmatch(r"\s*([0-9.]+)(px|rem)\s*(!important)?\s*", val)
            if radius:
                px = _px(radius[1], radius[2])
                if 0 < px < 100:
                    token = "var(--radius-control)" if px <= 8 else "var(--radius-panel)"
                    return f"{prop}: {token}{' !important' if radius[3] else ''}"

        def keyword(k: re.Match) -> str:
            token = _hex_token(prop, "FFFFFF" if k[1].lower() == "white" else "000000")
            return token or k.group(0)

        if not (_HEX_RE.search(val) or _KEYWORD_RE.search(val)):
            return m.group(0)
        return f"{prop}: {_KEYWORD_RE.sub(keyword, _HEX_RE.sub(hexsub, val)).strip()}"

    return _DECL_RE.sub(decl, css)


def rewrite_css_hex(text: str) -> str:
    """Replace known palette hex values with token variables in style attributes and <style> blocks."""
    text = _STYLE_BLOCK_RE.sub(lambda m: m[1] + _rewrite_declarations(m[2]) + m[3], text)
    return _STYLE_ATTR_RE.sub(lambda m: f'style="{_rewrite_declarations(m[1])}"', text)


# rgb()/rgba() forms of the same palette, mostly tinted backgrounds and rings.
_RGB_FAMILIES = {
    (255, 247, 237): "accent-soft-bg",  # orange-50
    (254, 215, 170): "accent-soft-bg",  # orange-200
    (251, 146, 60): "accent",  # orange-400
    (249, 115, 22): "accent",  # orange-500
    (234, 88, 12): "accent",  # orange-600
    (194, 65, 12): "accent",  # orange-700
    (154, 52, 18): "accent",  # orange-800
    (99, 102, 241): "accent",  # indigo-500
    (124, 58, 237): "accent",  # violet-600
    (239, 68, 68): "danger",  # red-500
    (220, 38, 38): "danger",  # red-600
    (34, 197, 94): "success",  # green-500
    (20, 184, 166): "success",  # teal-500
    (59, 130, 246): "info",  # blue-500
    (245, 158, 11): "warning",  # amber-500
    (234, 179, 8): "warning",  # yellow-500
}
_RGB_RE = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([0-9.]+%?)\s*)?\)")


def _alpha_pct(alpha: str) -> float:
    return float(alpha[:-1]) if alpha.endswith("%") else float(alpha) * 100


def rewrite_rgb(text: str) -> str:
    """rgba(249, 115, 22, 0.1) -> color-mix(in srgb, var(--accent) 10%, transparent), palette colours only."""

    def sub(m: re.Match) -> str:
        family = _RGB_FAMILIES.get((int(m[1]), int(m[2]), int(m[3])))
        if not family:
            return m.group(0)
        if family == "accent-soft-bg":
            return (
                "var(--accent-soft)"
                if m[4] is None
                else f"color-mix(in srgb, var(--accent-soft) {_alpha_pct(m[4]):g}%, transparent)"
            )
        if m[4] is None:
            return f"var(--{family})"
        return f"color-mix(in srgb, var(--{family}) {_alpha_pct(m[4]):g}%, transparent)"

    return _RGB_RE.sub(sub, text)
