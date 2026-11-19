# Clients, roles and Today

Phase 2 adds client records, the studio's roles, an audit log and the Today page.
Each client is a BrightBean **workspace**; the client record (`apps.clients`) hangs
off it one-to-one, so BrightBean's tables are unchanged.

## Roles

The brief's six roles are built from BrightBean's own roles, so its permission checks
keep working everywhere. Set them in **Settings → Members** (studio) and **Client
settings → Clients** (portal users).

| Brief role | Organization role | Role on each client (workspace) | What it opens |
|---|---|---|---|
| Studio Owner | Owner | Owner | Everything, including billing, finance, team, templates, integrations and the audit log. |
| Team Member | Member | Manager | Client records, content, approvals, projects, shoots, reports. **No billing** unless granted. |
| Freelancer | Member | Contributor, only on the clients they work on | Content and the projects and shoots they are assigned to. No client record, reports or billing. |
| Client Admin | Member (set by the portal invite) | Client + "Client admin" | Portal: content, approvals, reports, invoices, their company's portal users. |
| Client Reviewer | Member (set by the portal invite) | Client (built-in), or Client + "Client reviewer" | Portal: view and approve content. |
| Client Billing Contact | Member (set by the portal invite) | Client + "Client billing contact" | Portal: invoices and receipts only. |

The "Client admin", "Client reviewer" and "Client billing contact" roles are created
for the studio automatically (as BrightBean custom roles) the first time a client is
added.

### Permission keys

Added to BrightBean's per-client permissions (`apps/studio/permissions.py`):
`view_client_profile`, `manage_client_profile`, `view_projects`, `manage_projects`,
`view_shoots`, `manage_shoots`, `view_billing`, `manage_billing`, `view_internal`,
and the portal keys `portal_view_content`, `portal_approve`, `portal_view_reports`,
`portal_view_billing`, `portal_manage_users`. Projects, shoots and billing pages arrive
in Phases 4–5; their keys exist now so roles do not change later.

Studio-wide keys (organization level): `manage_clients`, `manage_team`,
`view_finance`, `manage_billing_settings`, `manage_templates`, `manage_integrations`,
`view_audit_log`, `override_approval`. Owners and admins hold all of them; a team
member can be given one individually (`StaffPermissionGrant`, in the Django admin until
the team settings page is restyled), for example `view_finance` for a bookkeeper.

None of these keys are API scopes: the REST API and MCP server stay limited to studio
staff, with BrightBean's existing scopes.

## How client data is kept apart

- Every page with a client in its URL goes through BrightBean's RBAC middleware
  (403 if the user is not a member of that client) and checks a permission key.
- Records under a client (contacts, services, retainers) are always fetched through
  the URL's client, so another client's id is a 404.
- Client users are kept to the portal by `ClientScopeMiddleware`: studio pages of their
  own client are 403 (except the approval endpoints the portal uses), studio-wide pages
  redirect to the portal.
- Internal notes, internal comments and the activity log are never rendered in the
  portal.
- `apps/studio/tests/test_demo_and_sweep.py` requests every URL that takes a client id
  (157 today, GET and POST) as another client's user and as a team member not on that
  client, and fails on anything but 403/404.

## Audit log

`apps.audit.AuditEvent` is append-only (saving an existing row raises). Entries are
written for approvals (submit, approve, request changes, reject, hold), publish
attempts and failures, membership and role changes, custom role changes, individual
grants, new clients and pipeline stage changes. Invoice changes join in Phase 5. Each
client's **Activity** tab shows its entries to staff with `view_internal`; the
studio-wide searchable log arrives with the settings work.

## Client records

- **Pipeline stage**: Lead, Proposal sent, Onboarding, Active, Paused, Former. The
  client list has saved views for each group.
- **Contacts** with main contact, approver and billing flags; linked to the person's
  portal account once invited.
- **Services**: social media management, branding, brand strategy, each proposed,
  active, paused or ended.
- **Retainer**: monthly fee (BZD by default), posts, reels, stories and shoot days per
  month, revision rounds, scope notes.
- **Health**: approvals overdue or due within 48 hours, posts planned for the next 30
  days against the retainer (12 a month if there is none), and accounts that need
  reconnecting. Unpaid invoices and goals are added when billing and goals exist rather
  than guessed now.

## Today

`/today/` (all clients) and `/today/<client>/` list, for the clients the signed-in
team member works on: approvals waiting (soonest due first, overdue flagged), posts in
the next 7 days, failed posts from the last 30 days, content gaps for the next 30 days
(active clients whose social media service is not paused), clients in Onboarding for
more than `STUDIO_ONBOARDING_STALL_DAYS` (14), and social accounts that need
reconnecting. Every item links to where it gets fixed. Studio staff land here after
signing in.

Times on Today and the client list are in the studio's time zone (`STUDIO_TIME_ZONE`);
times on a client's pages are in that client's time zone. BrightBean keeps Django's
`TIME_ZONE` at UTC, so these pages set it per request (`apps.studio.decorators`).

## Demo data

```bash
python manage.py seed_demo --reset
```

Creates "Brown Creative Studio (demo)" with Casa Maya Restaurant and Luma Skincare
(active) and Vista Realty (onboarding, stalled), three staff users, five client users,
posts in every status, approvals, internal and client comments, and 28 days of
Instagram figures. It refuses to run unless `DEBUG` is on (`--force` for a staging
server), removes only the demo organization and `@brown-demo.example` users, and prints
freshly generated passwords and portal links once. The social accounts are placeholders
without tokens: do not run the publishing worker against them.
