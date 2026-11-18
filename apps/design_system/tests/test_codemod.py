"""The template codemod maps BrightBean's Tailwind classes onto the studio tokens."""

import pytest

from apps.design_system.codemod import nearest_size, rewrite


def r(text: str) -> str:
    return rewrite(text).text


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # neutrals
        ('class="text-stone-900"', 'class="text-ink"'),
        ('class="text-stone-500"', 'class="text-ink-muted"'),
        ('class="text-stone-400"', 'class="text-ink-muted"'),
        ('class="bg-stone-50"', 'class="bg-canvas"'),
        ('class="hover:bg-stone-50"', 'class="hover:bg-sunken"'),
        ('class="bg-stone-100"', 'class="bg-sunken"'),
        ('class="border-stone-200"', 'class="border-hairline"'),
        ('class="border-stone-300"', 'class="border-control"'),
        ('class="divide-stone-100"', 'class="divide-hairline"'),
        ('class="bg-stone-900 text-stone-100"', 'class="bg-ink text-canvas"'),
        ('class="bg-white"', 'class="bg-surface"'),
        ('class="bg-white/90"', 'class="bg-surface/90"'),
        ('class="bg-black/50"', 'class="bg-scrim/50"'),
        ('class="placeholder-stone-400"', 'class="placeholder-ink-muted"'),
        # accent (orange) and the decorative colours that fold into it
        ('class="bg-orange-500 text-white"', 'class="bg-accent text-on-accent"'),
        ('class="hover:bg-orange-600"', 'class="hover:bg-accent-hover"'),
        ('class="bg-orange-50 text-orange-700"', 'class="bg-accent-soft text-accent"'),
        ('class="border-orange-200"', 'class="border-accent-line"'),
        ('class="ring-orange-100"', 'class="ring-accent-line"'),
        ('class="accent-orange-500"', 'class="accent-accent"'),
        ('class="bg-indigo-50 text-indigo-600"', 'class="bg-accent-soft text-accent"'),
        # status families
        (
            'class="bg-amber-50 text-amber-800 border-amber-200"',
            'class="bg-warning-soft text-warning border-warning/30"',
        ),
        ('class="bg-red-50 text-red-700"', 'class="bg-danger-soft text-danger"'),
        ('class="bg-red-500 text-white"', 'class="bg-danger text-on-accent"'),
        ('class="text-green-700 bg-green-50"', 'class="text-success bg-success-soft"'),
        ('class="text-blue-700 bg-blue-50"', 'class="text-info bg-info-soft"'),
        # opacity modifiers survive
        ('class="bg-orange-50/50"', 'class="bg-accent-soft/50"'),
    ],
)
def test_colours(before, after):
    assert r(before) == after


def test_variants_and_important_are_kept():
    assert r('class="group-hover:!text-stone-700 md:bg-white"') == 'class="group-hover:!text-ink md:bg-surface"'


def test_alpine_class_bindings_are_rewritten():
    before = """:class="active ? 'bg-orange-50 text-orange-700' : 'text-stone-500 hover:bg-stone-100'\""""
    after = """:class="active ? 'bg-accent-soft text-accent' : 'text-ink-muted hover:bg-sunken'\""""
    assert r(before) == after


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('class="text-white bg-black/60"', 'class="text-on-scrim bg-scrim/60"'),
        ('class="bg-[#1877F2] text-white"', 'class="bg-[#1877F2] text-on-scrim"'),
        ('class="bg-stone-900 text-white"', 'class="bg-ink text-canvas"'),
        (
            'class="text-white" style="background: var(--primary)"',
            'class="text-on-accent" style="background: var(--primary)"',
        ),
    ],
)
def test_white_text_follows_its_background(before, after):
    assert r(before) == after


def test_white_text_without_a_background_is_flagged_for_review():
    result = rewrite('<span class="text-white">x</span>')
    assert result.text == '<span class="text-on-accent">x</span>'
    assert result.changes[0].review


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('class="rounded"', 'class="rounded-control"'),
        ('class="rounded-lg"', 'class="rounded-control"'),
        ('class="rounded-xl"', 'class="rounded-panel"'),
        ('class="rounded-2xl"', 'class="rounded-panel"'),
        ('class="rounded-t-xl"', 'class="rounded-t-panel"'),
        ('class="rounded-[4px]"', 'class="rounded-control"'),
        ('class="rounded-[14px]"', 'class="rounded-panel"'),
        ('class="w-8 h-8 rounded-full"', 'class="w-8 h-8 rounded-full"'),
        ('class="px-4 py-2 rounded-full"', 'class="px-4 py-2 rounded-control"'),
        ('class="rounded-none"', 'class="rounded-none"'),
    ],
)
def test_radii(before, after):
    assert r(before) == after


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('class="p-4 shadow-sm border"', 'class="p-4 border"'),
        ('class="shadow p-4"', 'class="p-4"'),
        ('class="shadow-lg"', 'class="shadow-menu"'),
        ('class="shadow-2xl"', 'class="shadow-dialog"'),
        ('class="hover:shadow-md p-2"', 'class="p-2"'),
        ('class="shadow-none"', 'class="shadow-none"'),
        ('class="shadow-orange-500/20 p-1"', 'class="p-1"'),
    ],
)
def test_shadows(before, after):
    assert r(before) == after


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('class="text-xs"', 'class="text-12"'),
        ('class="text-sm"', 'class="text-14"'),
        ('class="text-base"', 'class="text-16"'),
        ('class="text-lg"', 'class="text-16"'),
        ('class="text-xl"', 'class="text-20"'),
        ('class="text-2xl"', 'class="text-24"'),
        ('class="text-3xl"', 'class="text-32"'),
        ('class="text-[11px]"', 'class="text-12"'),
        ('class="text-[0.8125rem]"', 'class="text-13"'),
        ('class="text-[0.9375rem]"', 'class="text-14"'),
        ('class="text-sm/6"', 'class="text-14/6"'),
    ],
)
def test_type_scale(before, after):
    assert r(before) == after


def test_scale_steps():
    assert [nearest_size(px) for px in (10, 12, 13, 14, 15, 16, 18, 20, 24, 30, 36, 48)] == [
        12,
        12,
        13,
        14,
        14,
        16,
        16,
        20,
        24,
        32,
        32,
        44,
    ]


def test_motion_is_clamped_and_glass_removed():
    assert r('class="duration-300 backdrop-blur-sm duration-75"') == 'class="duration-200 duration-120"'


def test_css_custom_properties_and_prose_are_left_alone():
    css = "box-shadow: var(--shadow-sm); color: var(--text-secondary);"
    assert r(css) == css
    prose = "<p>Rounded corners and a soft shadow.</p>"
    assert r(prose) == prose


def test_rewrite_is_idempotent():
    once = r('class="bg-orange-500 text-white rounded-xl shadow-sm text-sm px-3 rounded-full"')
    assert r(once) == once


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('style="color: #1C1917"', 'style="color: var(--text-primary)"'),
        ('style="color: #A8A29E;"', 'style="color: var(--text-secondary);"'),
        ('style="background: #F5F5F4"', 'style="background: var(--sunken)"'),
        ('style="background:#22C55E;"', 'style="background: var(--success);"'),
        ('style="border: 1px solid #E7E5E4"', 'style="border: 1px solid var(--hairline)"'),
        ('style="border-color: #FED7AA"', 'style="border-color: var(--accent-line)"'),
        ('style="background: #FFF7ED; color: #C2410C"', 'style="background: var(--accent-soft); color: var(--accent)"'),
        (
            'style="box-shadow: 0 0 0 3px #FECACA"',
            'style="box-shadow: 0 0 0 3px color-mix(in srgb, var(--danger) 30%, transparent)"',
        ),
        ('style="background: #fff"', 'style="background: var(--surface)"'),
        ('style="background: #7C3AED"', 'style="background: var(--accent)"'),
    ],
)
def test_hex_in_style_attributes(before, after):
    assert r(before) == after


def test_hex_in_style_blocks_and_platform_colours_kept():
    before = "<style>\n.x { color: #78716C; }\n.pi-facebook { background: #1877F2; }\na:hover { border-color: #F97316; }\n</style>"
    after = "<style>\n.x { color: var(--text-secondary); }\n.pi-facebook { background: #1877F2; }\na:hover { border-color: var(--accent); }\n</style>"
    assert r(before) == after


def test_svg_attributes_are_left_for_review():
    svg = '<path fill="#F97316" d="M0 0"/>'
    assert r(svg) == svg


def test_rgba_palette_colours_become_token_mixes():
    assert (
        r("background: rgba(249, 115, 22, 0.08);") == "background: color-mix(in srgb, var(--accent) 8%, transparent);"
    )
    assert (
        r("box-shadow: 0 0 0 3px rgba(239,68,68,0.15)")
        == "box-shadow: 0 0 0 3px color-mix(in srgb, var(--danger) 15%, transparent)"
    )
    assert r("color: rgb(23, 20, 18)") == "color: rgb(23, 20, 18)"
