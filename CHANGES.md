# Changes to upstream files

Brown Creative Studio is a fork of [BrightBean Studio](https://github.com/brightbeanxyz/brightbean-studio)
(AGPL-3.0), forked at upstream commit `14b92fc` on 2026-11-18.

Every change to a file that came from upstream is recorded here with its date and
reason, so each upstream merge can be reviewed against a known list. New features
live in new Django apps and new files; those are listed per entry but need no
merge care. See [docs/fork/README.md](docs/fork/README.md) for the working rules
and the upstream merge procedure.

## 2026-11-24 · Phase 5: portal and billing

### Upstream files changed by hand

| File | Change | Reason |
|---|---|---|
| `config/settings/base.py` | `apps.billing`, `apps.conversations` in `LOCAL_APPS`; the `i18n` context processor; `PublicLinkRateLimitMiddleware` and `LanguageMiddleware` after `ClientScopeMiddleware`. | New apps; per-person language; rate limits on links that work without signing in. |
| `config/urls.py` | `/portal/billing/`, `/portal/shoots/`, `/portal/calendar/`, `/portal/messages/` (before the portal's catch-all token pattern), `/billing/`, `/messages/`, `/invoice/` (public share links). | New pages. |
| `apps/notifications/models.py` (+ migration `0006`) | Event types `proposal_accepted`, `contract_signed`, `payment_submitted`, `client_message`. | The brief: extend notifications for the new modules. |
| `apps/client_portal/services.py` | The magic-link email's subject and body render in the client's language (`apps.studio.languages.for_user`). | Spanish portal and emails. |
| `templates/client_portal/portal_base.html` | `{% load i18n %}`, `lang` from the active language, every label translatable, the English/Español switch in the header (and at the foot on phones). | Spanish portal. |
| `templates/client_portal/dashboard.html` | Translatable strings; one include, `studio/portal/_coming_up.html` (next shoots, scheduled posts, invoices due). | Spanish portal; Home shows what is coming next. |
| `templates/client_portal/_client_card.html`, `approval_queue.html`, `published.html`, `activity.html`, `reports.html`, `magic_link_confirm.html`, `magic_link_expired.html`, `email/magic_link.txt`, `email/magic_link.html` | Strings wrapped for translation; dates day-first (`j M Y`, 24-hour time). | Spanish portal; Belize and Guatemala write dates day-first. |
| `Dockerfile` | Installs `gettext` and runs `compilemessages` before `collectstatic`. | Compiled translations in the image (`.mo` files are not committed). |
| `.github/workflows/ci.yml` | Compiles translations before pytest. | Language tests need the compiled catalog. |
| `.env.example` | `BILLING_PAYMENT_PROVIDER`, `BILLING_SIGNATURE_PROVIDER`, `BILLING_TAX_PROVIDER`. | Documented settings. |

Fork files changed in earlier apps: email subjects in `apps/brand/services.py` and
`apps/client_onboarding/services.py` render in the recipient's language; `apps/brand/portal.py`
strings are translatable; `apps/shoots/templates/shoots/call_sheet.html` uses a shared,
translated `_sheet.html`; the client header gains Billing (with `view_billing`) and Messages tabs;
client health gains an Invoices signal (only for people with `view_billing`); Today gains overdue
invoices and payments to confirm; the rail's Billing section needs `view_billing`.

### Behaviour changes to watch on upstream merges

- **Language per request.** `LanguageMiddleware` activates the person's language (their choice,
  else the studio's portal language for that client, else the browser's, else English). Studio
  screens from BrightBean are still English; dates and numbers follow the language
  (`config/formats/es`: decimal point and comma thousands, as in Belize and Guatemala).
- **Public links are rate-limited**: 60 requests a minute per IP, for signed-out visitors on
  `/call-sheet/`, `/invoice/` and `/portal/` (magic links).
- `ClientScopeMiddleware` client namespaces add `billing_portal`, `billing_public`, `shoots_portal`,
  `planning_portal`, `conversations_portal`; `studio:set_language` is a client view.

### New files

- `apps/billing/`: service catalog; proposal and contract templates; proposals (accept or
  decline in the portal); contracts (typed signature with name, email, time, IP and a SHA-256 of
  the text; e-signature adapter with a mock); invoices and credit notes (deposits, milestones,
  one-time, retainers, extras, line and invoice discounts, taxes, several currencies with the
  exchange rate stored); payments (recorded by the studio, receipts uploaded by the client and
  confirmed, online card payments behind an adapter with a mock); certified tax documents behind
  an adapter (never made up); statuses Draft, Sent, Viewed, Partly paid, Paid, Overdue, Void;
  reminders on an editable schedule; monthly retainer drafts; the finance view; invoice PDFs that
  say they are not certified tax documents. Accepting a proposal creates the contract, the
  deposit invoice, the service lines and retainer, and onboarding (or projects for an existing
  client); signing and paying the deposit tick the onboarding steps.
- `apps/conversations/`: messages between client and studio, each tied to an item (invoice,
  proposal, contract, shoot, post…); billing topics only for people who see billing.
- Portal sections: Calendar (`apps/planning/portal.py`), Shoots with a preparation checklist the
  client can tick (`apps/shoots/portal.py`), Billing, Messages, and "Coming up" on Home.
- `apps/studio/languages.py`, `Preference` model (language per person, not a new User column),
  `studio/_language_choice.html`, `locale/es/LC_MESSAGES/django.po` (portal and emails in Spanish).
- Docs: [docs/fork/portal-and-billing.md](docs/fork/portal-and-billing.md).

### Changed test expectations

None. BrightBean's tests pass unchanged.

## 2026-11-23 · Phase 4: content operations

### Upstream files changed by hand

| File | Change | Reason |
|---|---|---|
| `config/settings/base.py` | Added `apps.reviews`, `apps.planning`, `apps.shoots`, `apps.assets` to `LOCAL_APPS`. | New apps. |
| `config/urls.py` | `/portal/review/` (before the portal's catch-all token pattern), `/reviews/`, `/planning/`, `/shoots/`, `/call-sheet/`, `/assets/`. | New pages. |
| `requirements.txt` | `fpdf2>=2.8,<3.0` (LGPL-3.0, compatible with AGPL-3.0). | Call sheet PDFs. |
| `apps/approvals/services.py` | `approve_post` asks `reviews.check_client_stage` for each platform version: only a client approves the client stage, clients approve only what is waiting for them, and an approval rule's named approvers are respected. Versions it may not move are skipped; if none moved it raises with the reason. | Approval gap: studio staff could approve on the client's behalf, and a client could approve versions still in studio review. |
| `apps/approvals/views.py` | `add_comment` keeps the created comment and calls `reviews.attach_pin` (image point or video time). | Pinned feedback. |
| `apps/composer/views.py` | The status transition endpoint refuses `approved` without `check_client_stage`, and `scheduled`/`publishing` (except retrying a failed post) without `schedule_block_reason`, answering 400 with the reason. | Every route into scheduling checks that the required approvals cover the exact content. |
| `apps/calendar/views.py` | Drag-to-reschedule and bulk publish call `schedule_block_reason` before promoting a post. | Bulk publish uses `bulk_update`, which skips the model signal that otherwise guards scheduling. |
| `apps/workspaces/views.py` | New workspaces start with `approval_workflow_mode = required_internal_and_client`. | The studio's default: internal review, then one client approval before scheduling. |
| `templates/approvals/partials/comment_list.html` | Shows a comment's pin ("photo.jpg, at 0:12"). | Pinned feedback. |
| `templates/calendar/partials/month_grid.html`, `week_grid.html`, `day_grid.html`, `templates/organizations/partials/day_grid.html`, `week_grid.html` | `{% load planning %}`; the chip's title and a screen-reader label carry `{% production_label pp %}` ("Shot needed · Draft"). | Production stage and approval status together on the calendar. |
| `templates/calendar/partials/list_view.html` | The status cell adds the production stage (`{% production_stage post %}`). | Same, in the list view. |
| `templates/calendar/calendar.html` | A "Plan" button beside the view switch, for people with `view_plan` (`shell_can_plan`). | The 13-week plan is one click from the calendar. |
| `templates/media_library/_asset_detail_panel.html` | "Rights and use" button (client libraries only). | Asset rights and where an asset is used. |
| `templates/layouts/workspace_settings.html` | Two items after Client Portal: "Approvers and turnaround" (`reviews:rule`) and "Portal links" (`reviews:portal_links`). | Reach the approval rule and link revocation from the client's settings. |

### Behaviour changes to watch on upstream merges

- **Model signals guard approvals** (`apps/reviews/signals.py`). A `PlatformPost` saved into
  `scheduled` or `publishing` from another status (except a failed retry) is refused with
  `ApprovalRequiredError` unless the required approvals cover its exact content. Any change
  to approved content (title, caption, first comment, tags, media, media file or version,
  per-platform overrides) sends approved, pending-client, scheduled, failed and on-hold
  versions back to studio review, with an audit event. Code that writes in bulk
  (`bulk_update`, `QuerySet.update`) skips signals and must call `schedule_block_reason`
  itself, as the calendar now does. `apps.reviews.services.bypass()` is for fixtures only
  (the demo uses it for posts created already published).
- `ApprovalRecord` is written for every `ApprovalAction` (signal), with the exact
  `PostVersion` and content fingerprints. A submission moves the post's production stage to
  "Ready for review" (`apps/planning/signals.py`).
- New workspace permission `view_plan` (owner, manager, editor, viewer; not contributors or
  clients) opens the plan, campaigns, key dates, asset rights and a post's review history.
  Freelancers keep to their tasks and the shoots they are crew on, as the brief says. Custom
  roles need the key added to see these pages.
- `ClientScopeMiddleware` adds `reviews_portal` and `shoots_public` to the client
  namespaces. The public call sheet needs no login but only works while its share link is on.

### New files

- `apps/reviews/`: approval records with exact versions, comment pins, approval rules
  (named approvers, turnaround), the schedule guard and reopen-on-change, client-stage
  checks, override with reason, the post's full history, the client's batch review
  (week, month, everything waiting) and portal link revocation.
- `apps/planning/`: content plans (stage, format, objective, hook, script, call to action,
  dates, owner, campaign), campaigns, key dates (Belize public holidays built in), the
  13-week plan per client and across clients, warnings (gaps, one-sided pillar or format,
  no media or no approval close to the date, rights ending before the post).
- `apps/shoots/`: shoots and shot lists, booking conflicts, readiness, shot lists from
  planned posts, on-set mode that works offline (`static/shoots/onset.js`, a service worker),
  matching uploads to shots by code (scanned first), follow-up tasks, content runway, call
  sheets as a page, a share link and a PDF.
- `apps/assets/`: usage rights per media asset (source, usage, licence, releases, expiry)
  and where the asset is used.
- Today gains upcoming shoot days with what is still missing, and planned posts no shoot
  covers. The rail's Calendar section includes the Plan; Approvals includes reviews.
- Docs: [docs/fork/content-operations.md](docs/fork/content-operations.md).

### Changed test expectations

- `apps/approvals/test_workflow.py::TwoStageFlowTests`: the client-stage approval is now made
  by a client user (added in `setUp`). Studio staff approving the client stage is refused by
  design.
- `tests/test_member_role_authorization.py`: a `_studio_approved()` helper on
  `RoleAuthorizationBase` records a real studio approval (an `ApprovalAction`), used by
  `TransitionFollowUpTests::test_an_approved_post_can_still_be_scheduled_under_required_approval`
  and `ApprovalWorkflowGateTests::test_an_approved_post_is_scheduled_by_its_author`. Both wrote
  `status="approved"` straight to the database with no approval behind it, which the schedule
  guard now refuses. What they check (an approved post can be scheduled) is unchanged.

## 2026-11-23 · Modern redesign

The owner asked for a more modern interface, replacing Phase 1's editorial direction.
Tokens and components changed; markup changed only where noted. Details and the new
rules are in [docs/fork/design-system.md](docs/fork/design-system.md).

### Upstream files changed by hand

| File | Change | Reason |
|---|---|---|
| `theme/static_src/src/styles.css` | New palette (warm-neutral canvas, white surface, `--hairline-strong`), `--shadow-card`/`--shadow-raised`/`--shadow-control` per theme, radii 8 px and 12 px, Geist as the only family (Instrument Serif `@font-face` removed), headings semibold by default, rail items as raised chips, soft focus halo on inputs, shadows on the upstream button/auth helpers. | Modern look. Contrast still verified by `apps/design_system/tests/test_tokens.py`. |
| `templates/base.html` | Instrument Serif preload removed; rail loses its right border on desktop; the page column gets `.app-frame` (raised white frame on the canvas). | Modern shell. |
| `static/favicon/site.webmanifest` | `background_color` follows the new canvas. | Matches the app. |
| `templates/media_library/library_index.html` | `aria-label`/`aria-pressed` on the grid/list buttons, `aria-label` on the sort select. | axe `button-name`, `select-name` (found while checking the redesign). |
| `templates/inbox/partials/_filter_bar.html` | `aria-label` on the four filter selects. | axe `select-name`. |
| `templates/composer/partials/kanban_column.html` | `type`, `aria-label`, `aria-expanded` on the column menu button. | axe `button-name`. |

Fork files changed: `components.css` (type, buttons, inputs, panels, pill status labels,
tabs, tables, empty states, wordmark; new `.app-frame`, `.segmented`/`.segment`,
`.stat-label`/`.stat-value`), the rail brand and top bar partials, the style guide,
and the sub-navigation of Today, Clients, Plan, Brand, task status, post stage and the
portal review period (now `.segmented`). `static/fonts/instrument-serif/` is deleted
(fork-added in Phase 1, no longer used).

### Changed test expectations

- `apps/design_system/tests/test_tokens.py::test_spec_values_are_exact` checks the new
  anchor values (canvas, surface, text, control border); the accent is unchanged. All
  contrast checks are unchanged and pass.

## 2026-11-19 · Phase 3: client setup

### Upstream files changed by hand

| File | Change | Reason |
|---|---|---|
| `config/settings/base.py` | Added `apps.projects`, `apps.brand`, `apps.client_onboarding` to `LOCAL_APPS`, and the `apps.studio.context_processors.portal` context processor. | New apps; portal sections from the fork's apps. |
| `config/settings/test.py` | `PRIVATE_MEDIA_ROOT` under `test_media/`, `MALWARE_SCAN_BACKEND = "mock"`. | Tests never write private files into the checkout, and never depend on a scanner. |
| `config/urls.py` | Includes `/projects/`, `/brand/`, `/client-onboarding/`, and `/portal/brand/`, `/portal/onboarding/`, `/portal/projects/` **before** `apps.client_portal.urls`. | The portal's last pattern is a catch-all magic-link token (`/portal/<token>/`), which would otherwise swallow `/portal/onboarding/`. |
| `templates/client_portal/portal_base.html` | One loop over `portal_sections` after the Activity tab; Django messages and `portal_notices` at the top of `<main>`; the Activity tab is only current in the `client_portal` namespace. | The fork's portal sections (Getting started, Projects, Brand Hub) appear only when they have content, without editing the portal's views. The portal never showed messages, so confirmations from new actions were invisible. |
| `.env.example`, `.gitignore`, `.dockerignore` | Private files, malware scanning and onboarding settings; `private_media/` ignored. | Documented settings; client files never committed or copied into images. |

`docker-compose.studio.yml` (fork-owned) gains a `private_media` volume and an optional
`clamav` service (`--profile scan`).

### Behaviour changes to watch on upstream merges

- `ClientScopeMiddleware` now lets client users reach `studio:file` (signed downloads, which
  check access themselves) and BrightBean's `onboarding:connection_*` pages (onboarding step 8
  sends clients there to connect their social accounts).
- Onboarding creates client portal users itself (no password; they sign in by emailed magic
  link, using BrightBean's `MagicLinkToken`) with the client role presets from Phase 2. Studio
  staff of the same studio are refused.
- `WorkspaceMembership` permission dicts gain `manage_brand` (owner, manager, editor).

### New files

- `apps/projects/`: projects, tasks (checklists, comments, links, dependencies), templates,
  saved filters; list, board, timeline and calendar views; workload; retainer delivery; portal
  Projects page (milestones and progress only).
- `apps/brand/`: brand profile, seven brand stages with client sign-off, brand files; studio
  pages and the portal Brand Hub.
- `apps/client_onboarding/`: onboarding templates per service line, runs, steps, participants;
  studio progress page; guided portal flow; hourly reminder task; completion.
- `apps/studio/files.py`, `scanning.py`, `portal.py`, `forms.py`: private files with signed
  short-lived links, malware scanning adapter (ClamAV or mock), portal sections and emails.
- `static/js/projects-board.js`; new components in `theme/static_src/src/components.css`
  (meter, board, timeline, month grid, swatch).

### Changed test expectations

| Test | Change | Reason |
|---|---|---|
| `apps/about/tests/test_production_settings.py` | The "configured deployment" fixture sets `MALWARE_SCAN_BACKEND = "clamd"`; a case checks the new `about.W008`. | New deploy check: uploads must be scanned in production. |
| `apps/brand/tests/test_brand.py` etc. | New. | Phase 3. |

## 2026-11-19 · Phase 2: clients and roles

### Upstream files changed by hand

| File | Change | Reason |
|---|---|---|
| `config/settings/base.py` | Added `apps.audit` and `apps.clients` to `LOCAL_APPS`; added `apps.studio.middleware.ClientScopeMiddleware` and `apps.audit.context.AuditContextMiddleware` right after `RBACMiddleware`. | Client users stay in the portal (server-side); audit entries know who acted. |
| `config/urls.py` | Includes `apps.clients.urls` (`/clients/`) and `apps.studio.urls` (`/today/`). | New pages. |
| `apps/accounts/views.py` | `dashboard` sends studio staff to Today (3 lines at the top). | The brief's start page. Client-only users never reach it (the middleware sends them to the portal). |
| `.env.example` | `STUDIO_TIME_ZONE`, `STUDIO_COUNTRY`, `BILLING_CURRENCIES`, `STUDIO_ONBOARDING_STALL_DAYS`. | Documented studio defaults. |

No upstream model, migration or permission table was edited. The new permission keys
are added at start-up by `apps.studio.permissions.register()` (into
`BUILTIN_ROLE_PERMISSIONS`, `BUILTIN_ORG_PERMISSIONS` and `ORG_PERMISSION_KEYS`), and are
deliberately **not** added to `PERMISSION_KEYS`, which doubles as the Agent API scope
list: the REST API and MCP stay limited to what BrightBean already exposes to staff.
After an upstream merge, `apps/studio/tests/test_roles.py` fails if a new upstream role
lacks the studio keys.

### Behaviour changes to watch on upstream merges

- A client user (only `client` workspace roles in an organization) who signs in with a
  password is given the portal session a magic link would give, for their last (else
  first) client workspace. BrightBean's portal otherwise only accepts magic-link
  sessions. Code: `apps/studio/middleware.py`.
- Client users get 403 on every studio URL of their own workspace except the approval
  endpoints the portal posts to (`CLIENT_WORKSPACE_VIEWS`), and are redirected to the
  portal from studio-wide pages. A new upstream view needs no change: it is blocked
  unless added to the allow lists.

### New files

- `apps/studio/`: `permissions.py` (keys, role defaults, client role presets),
  `StaffPermissionGrant` model, `middleware.py`, `decorators.py`
  (`studio_staff_required`, `require_studio_permission`), `today.py` and the Today page,
  `demo.py` and `seed_demo`, `services.py`.
- `apps/audit/`: append-only `AuditEvent`, actor middleware, signal handlers for
  approvals, publishing, memberships, roles and grants.
- `apps/clients/`: `ClientProfile`, `Contact`, `ServiceLine`, `Retainer`, health
  signals, client list and client pages.

### Changed test expectations

None. New tests: `apps/studio/tests/test_roles.py`, `test_today.py`,
`test_demo_and_sweep.py` (157 workspace URLs × GET/POST refused for another client's
user and for staff not on that client), `apps/clients/tests/test_clients.py`,
`apps/audit/tests/test_audit.py`.

## 2026-11-19 · Phase 1: design system

### Mechanical changes (reproducible)

`python manage.py apply_design_tokens` (`apps/design_system/codemod.py`) rewrote every
template under `templates/` and `apps/*/templates/` except emails, plus the markup
strings in `apps/client_portal/views_admin.py`, `apps/composer/forms.py`,
`apps/composer/views.py` and `apps/members/views.py`:

- Tailwind palette classes (`orange-*`, `amber-*`, `stone-*`, `gray-*`, status and
  decorative colours, `white`, `black`) to semantic utilities (`bg-surface`,
  `text-ink-muted`, `bg-accent`, `text-danger`, `bg-scrim/50`…).
- `text-xs`…`text-7xl` and arbitrary sizes to the 12/13/14/16/20/24/32/44 scale;
  `rounded-*` to `rounded-control` (6 px) or `rounded-panel` (10 px); pill buttons to 6 px;
  small shadows removed, large ones to `shadow-menu`/`shadow-dialog`; hover lifts and
  `backdrop-blur` removed; durations clamped to 120–200 ms.
- Palette hex, `rgb()` and `white`/`black` keywords in `style` attributes and `<style>`
  blocks to token variables; CSS font sizes and radii to the scales.

After an upstream merge, run the command and review its report; the guard tests in
`apps/design_system/tests/test_guards.py` fail while anything is left.

### Upstream files changed by hand

| File | Change | Reason |
|---|---|---|
| `theme/static_src/src/styles.css` | Rewritten token layer: brown scale, semantic tokens for light and dark, upstream token names kept as aliases, Tailwind palette/sizes/radii/shadows reset to the studio scales, self-hosted fonts; component classes restyled on tokens; imports `components.css`. | The brief's design tokens and dark theme. |
| `templates/base.html` | Wordmark, registry rail (`studio/shell/_rail_nav.html`), top bar with client switcher and command menu, quiet "New" menu, theme choice in the account menu, review banner and "Connect channels" block removed, vendored flatpickr/Chart.js/SortableJS, chart colours read from tokens, main area without the floating card. | App shell from the brief; no CDN. |
| `config/settings/base.py` | Added `apps.design_system`, `apps.studio` and the `shell` context processor; CSP no longer allows `cdn.jsdelivr.net`; OAuth scope text uses `SITE_NAME`. | New apps; strict CSP; branding. |
| `config/urls.py` | Includes `apps.design_system.urls` (`/design-system/`). | Style guide. |
| `apps/api/api.py` | Title from `SITE_NAME`; `docs_decorator` lets only the Swagger page load from the CDN. | Branding; CSP. |
| `apps/approvals/templatetags/approval_extras.py` | Status badge colours are tokens; labels in sentence case. | Status = word + colour, both themes. |
| `apps/organizations/views.py` | Clients without a brand colour get a colour-blind-checked categorical colour instead of orange. | No orange; distinguishable clients. |
| `apps/analytics/services.py`, `apps/api/schemas.py`, `apps/mcp/handlers.py`, `apps/members/services.py`, `apps/oauth_server/metadata.py`, `apps/composer/curated_feeds.py` | "BrightBean" in user-facing strings replaced with `SITE_NAME` or neutral wording. | Branding. |
| `templates/calendar/calendar.html`, `calendar/partials/publish_calendar_shell.html`, `_post_actions_menu.html`, `_no_channels_empty_state.html`, `month_grid.html`, `event_form.html`, `publish_approvals.html` | Display title, one primary action ("New post"), `aria-pressed` view toggle, next-action empty state, labelled icon controls, adjacent-month days readable, event presets from a validated palette, accent/secondary buttons. | Design and WCAG 2.2 AA. |
| `templates/approvals/partials/_approval_row.html`, `_toasts.html` | Secondary row actions, labelled checkboxes; toasts on tokens with `role="status"`/`alert`. | One primary action; both themes. |
| `templates/composer/compose.html`, `create_landing.html`, `categories.html`, `partials/feeds_tab.html` | Sentence-case actions, accent buttons, labelled back button and focusable preview region; BrightBean illustration removed. | Design, accessibility, branding. |
| `templates/client_portal/portal_base.html`, `dashboard.html`, `_client_card.html`, `approval_queue.html`, `magic_link_confirm.html` | Client identity header with a discreet studio mark and the client colour as a detail; tabs; 16 px body; Approve as the one primary action; no lifts or pulses. | Portal brief. |
| `templates/organizations/cross_calendar.html`, `templates/media_library/shared_library.html` | Extend `base.html` instead of the settings layout; display titles. | "All clients" pages are primary destinations. |
| `templates/analytics/_hero_chart.html`, `_post_detail.html`, `_page_header.html` | Token colours; display title. | Both themes. |
| `templates/account/login.html`, `signup.html`, `accept_terms.html`, `templates/oauth2_provider/base.html`, `authorize.html`, `templates/members/accept_invite.html`, `invite_expired.html` | Wordmark instead of the BrightBean logo; green glows removed. | Branding. |
| Account, invitation and notification emails | `{% site_name %}` / `current_site.name` instead of "Brightbean"; brown instead of orange. | Branding. |
| `templates/onboarding/partials/_checklist.html` | Starts collapsed on phones; ring colour from tokens. | It covered the whole phone screen. |
| Page titles across templates | " - Brightbean" / "· BrightBean Studio" to "· {{ SITE_NAME }}". | Branding. |
| `static/favicon/*` | New monogram (Instrument Serif "B" on the accent) and manifest. | Branding. |

Deleted: `templates/partials/_review_banner.html` (Trustpilot card for BrightBean),
`static/img/brightbean-logo.webp`, `static/img/brightbean-studio-logo.webp`.
Not changed on purpose: the Intelligence templates (the add-on is off and unreachable),
internal identifiers (`bb_studio_` keys, `X-Brightbean-Email-Class`, User-Agent strings)
and model choice labels (changing them adds migrations to upstream apps; they move to
sentence case with the translation work).

### New files

- `apps/design_system/`: codemod and `apply_design_tokens` command, guard and contrast
  tests, `{% status %}` tag, page-header/state/status partials, style guide at
  `/design-system/`, designed 403/404/500 pages.
- `apps/studio/`: navigation registry, shell context processor, rail/top bar/client
  switcher/command menu/theme templates.
- `theme/static_src/src/components.css`, `static/js/studio-shell.js`.
- `static/fonts/` (Instrument Serif, Geist; OFL), `static/js/vendor/` (flatpickr 4.6.13,
  Chart.js 4.4.6, SortableJS 1.15.6; MIT, licences included).

### Changed test expectations

| Test | Change | Reason |
|---|---|---|
| `apps/common/tests/test_sidebar_connect_channels.py` | Deleted. | The rail no longer suggests platforms to connect; accounts are connected from the client's settings. `apps/studio/tests/test_shell.py` asserts the block is gone. |
| `apps/common/tests/test_review_banner.py` | Four "banner is shown" tests replaced by one asserting it never renders; dismissal-endpoint tests kept. | The review card is removed. |
| `apps/social_accounts/tests/test_views.py` | Expects "Brown Creative Studio" where it expected "BrightBean" in the account-removal copy. | Branding. |
| `apps/analytics/tests/test_services.py` | Same, in the "calculated by" note. | Branding. |
| `apps/social_accounts/tests/test_youtube_compliance.py` | `test_history_keeps_180_days…` builds its dates on the America/Los_Angeles calendar, as the purge does. | Upstream flake: with UTC dates it failed every day between 00:00 and 08:00 UTC (also on unmodified upstream code). |

## 2026-11-18 · Phase 0: fork and baseline

### Upstream files changed

| File | Change | Reason |
|---|---|---|
| `config/settings/base.py` | Added `apps.about` to `LOCAL_APPS` and `apps.about.context_processors.site_identity` to the context processors; `SIGNUP_MODE` now defaults to `invite`; star-imports `config/settings/studio.py` on its last line. | Fork settings live in their own module. Sign-up must fail closed: upstream's default lets anyone on the internet create an account. |
| `config/settings/production.py` | Raises `ImproperlyConfigured` when `SIGNUP_MODE=open`. | Production must never accept public sign-ups. |
| `config/urls.py` | Includes `apps.about.urls` (`/about/source/`, `/legal/terms/`, `/legal/privacy/`). | AGPL source page and the studio's own legal pages. |
| `apps/accounts/middleware.py` | Added `/legal/` and `/about/` to `TosAcceptanceMiddleware`'s exempt prefixes. | A user asked to accept the terms must be able to read them, and the source page must reach everyone. |
| `templates/base.html` | Includes the sidebar "Source code" link above the organization footer; the signed-out `<main>` became a flex column and includes the public footer. | AGPL-3.0 §13: every user sees a link to the running source. |
| `templates/client_portal/portal_base.html` | Includes the public footer after `<main>`. | Same, for portal users. |
| `templates/client_portal/magic_link_confirm.html`, `templates/client_portal/magic_link_expired.html` | Include the public footer; `<body>` became `flex-col` so it sits under the card. | Same, for clients arriving from a link. |
| `templates/account/accept_terms.html` | Includes the public footer; Terms and Privacy links use `TERMS_URL` / `PRIVACY_URL` instead of brightbean.xyz. | AGPL link; the studio's policies, not BrightBean's. |
| `templates/account/signup.html` | Terms and Privacy links use `TERMS_URL` / `PRIVACY_URL`. | The studio's policies, not BrightBean's. |
| `templates/members/accept_invite.html`, `templates/members/invite_expired.html`, `templates/notifications/unsubscribe.html`, `templates/notifications/unsubscribed.html`, `templates/oauth2_provider/base.html`, `templates/ninja/swagger_cdn.html` | Include the public footer before `</body>`. | These pages render their own `<html>` shell; each must carry the source link. A test (`apps/about/tests/test_pages.py`) fails if a new standalone shell lacks it. |
| `Dockerfile` | `ARG SOURCE_COMMIT` / `ENV SOURCE_COMMIT`, declared after the dependency layers. | The image records the commit it was built from, for the source link. |
| `.env.example` | `SIGNUP_MODE=invite`; added `SENTRY_DSN=` (read by settings but missing from the example); appended a Brown Creative Studio section. | Safe defaults and the new settings. |
| `README.md` | Short fork notice at the top. | Says what this repository is and points to the fork docs. |
| `.pre-commit-config.yaml` | `check-yaml` excludes `docker-compose.studio.yml`. | That file uses Compose's `!reset` tag, which a plain YAML loader rejects; `docker compose config` validates it instead. |

### New files

- `apps/about/`: source page, legal fallback pages, the `site_identity` context
  processor, running-commit resolution, production deploy checks
  (`manage.py check --deploy`), and a post-migrate sync of the
  `django.contrib.sites` record (upstream migration `accounts.0004` names it
  "Brightbean", which allauth puts into email subjects).
- `config/settings/studio.py`: the fork's settings.
- `.dockerignore`: upstream had none, so `COPY . .` baked `.env` (secrets) and
  local uploads into the image.
- `docker-compose.studio.yml`: production overlay. Stops publishing Postgres
  (5432, password `postgres`) and Gunicorn (8000, plain HTTP) on the host, takes
  the database password from `.env`, passes `APP_DOMAIN` to Caddy, adds backups.
- `deploy/deploy.sh`, `deploy/backup/`: deploy script and nightly backup service.
- `docs/fork/`: fork rules, baseline report, deployment guide.

### Changed test expectations

None. All 2,793 upstream tests pass unchanged (`config/settings/test.py` already
pins `SIGNUP_MODE=open` for its own signup tests).
