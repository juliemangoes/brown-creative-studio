# Design system

The studio's interface is built from a small set of tokens and components. The
live reference is the style guide at **`/design-system/`** (studio staff only),
which renders every component in the current theme.

## Tokens

Defined once, for light and dark, in `theme/static_src/src/styles.css`. Templates
use them through Tailwind utilities; Tailwind's own palette, sizes, radii and
shadows are switched off, so anything else produces no CSS.

| Use | Utilities |
|---|---|
| Backgrounds | `bg-canvas`, `bg-surface`, `bg-sunken` |
| Lines | `border-hairline` (dividers), `border-control` (inputs) |
| Text | `text-ink`, `text-ink-muted` (the only two text colours that meet AA everywhere) |
| Action | `bg-accent`, `text-on-accent`, `bg-accent-soft`, `border-accent-line`, `hover:bg-accent-hover` |
| Status | `text-success`/`bg-success-soft`, `warning`, `danger`, `info` in the same pattern |
| Overlays | `bg-scrim/50`, `text-on-scrim` (over media) |
| Type | `text-12` … `text-44` (12, 13, 14, 16, 20, 24, 32, 44); `font-display` for page titles, client names and report headlines only |
| Shape | `rounded-control` (6 px), `rounded-panel` (10 px), `rounded-full` for avatars and dots |
| Elevation | `shadow-menu`, `shadow-dialog`, nothing else |

In CSS, use the variables (`var(--surface)`, `var(--text-secondary)`…). Never use
the brand scale (`--brand-*`) or hex values in templates; the guard tests reject them.
User-chosen data colours (calendar events, content pillars, client colours) are data,
not tokens, and are drawn from the colour-blind-checked palette in
`templates/calendar/partials/event_form.html`.

## Components

`theme/static_src/src/components.css`: `.btn` with `.btn-primary`, `.btn-secondary`,
`.btn-quiet`, `.btn-danger`, `.btn-danger-quiet`, `.btn-sm`, `.btn-icon`; `.field`,
`.label`, `.input`, `.select`, `.textarea`, `.help`, `.field-error`; `.panel`,
`.panel-header`, `.panel-body`, `.well`; `.status` with a tone; `.tabs`/`.tab`;
`.table`; `.menu`/`.menu-item`; `.dialog`; `.side-panel`; `.toast`; `.stepper`/`.step`;
`.state`; `.page-title`, `.client-name`, `.section-title`, `.meta`, `.kbd`.

Template partials and tags (`apps/design_system`):

```django
{% include "ds/_page_header.html" with eyebrow=workspace.name title="Calendar" action_url=url action_label="New post" %}
{% include "ds/_state.html" with kind="empty" title="…" body="…" action_url=url action_label="…" %}
{% load ds %}{% status "post" platform_post.status %}   {# also "production", "invoice", "pipeline" #}
```

## Rules

- One primary action per screen; row and card actions are secondary.
- Status is always a word plus a colour.
- Every screen designs its loading, empty, error and no-permission states; empty
  states say what to do next.
- Motion 120–200 ms; `prefers-reduced-motion` turns it off.
- Icon-only controls carry an `aria-label`; focus is always visible (2 px accent outline).

## Shell

`apps/studio/navigation.py` lists the rail's sections. A section appears as soon as
its URL resolves, so a new module only needs its URL names to match (for example
`studio:today`, `clients:list`, `shoots:list`). The command menu (Ctrl/Cmd K) is
built from the same registry.

## Checking a change

```bash
python manage.py apply_design_tokens --check   # templates on the tokens
pytest apps/design_system apps/studio          # guards, contrast, shell
```

Then look at `/design-system/` and the changed screens in both themes (account menu,
Theme) and at phone width.
