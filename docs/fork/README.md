# Brown Creative Studio: working on the fork

Brown Creative Studio extends [BrightBean Studio](https://github.com/brightbeanxyz/brightbean-studio)
into a multi-client studio workspace and client portal. It stays on BrightBean's
stack (Django 5, HTMX, Alpine.js, Tailwind CSS 4, PostgreSQL,
django-background-tasks) and under its license (AGPL-3.0).

| Document | What it covers |
|---|---|
| [`CHANGES.md`](../../CHANGES.md) | Every change to an upstream file, with date and reason |
| [`baseline.md`](baseline.md) | Phase 0 baseline: test results, and where the build brief no longer matches the code |
| [`deployment.md`](deployment.md) | Production on a VPS with Docker Compose, S3 storage, email, monitoring, backups |
| [`design-system.md`](design-system.md) | Tokens, components, the shell registry and how to check a change |
| [`clients-and-roles.md`](clients-and-roles.md) | Roles and permission keys, how client data is kept apart, audit log, Today, demo data |
| [`client-setup.md`](client-setup.md) | Onboarding, projects and tasks, brand profile and Brand Hub, private files and malware scanning |
| [`content-operations.md`](content-operations.md) | Planning, shoots and on-set mode, asset rights, approvals and a post's full history |
| [`portal-and-billing.md`](portal-and-billing.md) | The client portal's sections, English and Spanish, proposals, contracts, invoices, payments and messages |

## Decisions that differ from the build brief

Made by the studio owner; revisit them here if things change.

| Date | Decision | Brief said |
|---|---|---|
| 2026-11-23 | Modern interface: one sans family, neutral canvas, raised page frame ([design-system.md](design-system.md)). | Editorial direction with a serif display face. |
| 2026-11-24 | **No two-factor sign-in** for studio users. BrightBean's unused `totp_secret` field stays as it is. | Require two-factor authentication for studio users. |
| 2026-11-24 | **No taxes on invoices for now.** The tax rate defaults to 0; set a tax name and rate in Billing settings when the studio starts charging one. | (Taxes supported; rate not given.) |
| 2026-11-24 | **Bank transfer only.** Clients pay by online bank transfer and upload the receipt; no card provider (`BILLING_PAYMENT_PROVIDER=none`). The mock card checkout exists only for testing. | Payment provider not chosen; launch with bank transfer and receipts. |
| 2026-11-24 | **Posts per month come from the package sold.** Monthly catalog items carry posts, reels, stories and shoot days; accepting a proposal sets the client's retainer scope from them. No default of 12. | (Default not specified.) |
| 2026-11-24 | BrightBean's "Get started" checklist pop-up is removed. | (Not covered.) |

## Rules that keep upstream merges cheap

1. **New features go in new Django apps** under `apps/`, linked to a client through
   `Workspace` (`apps.common.managers.WorkspaceScopedModel`, or a one-to-one profile).
2. **Do not add columns to BrightBean's tables.** Put new data in a linked model.
3. **When an upstream file must change, keep the change small**, mark it with a
   `Brown Creative Studio` comment where a comment fits, and add a row to
   `CHANGES.md` in the same commit.
4. **Fork settings go in `config/settings/studio.py`**, not in `base.py`.
5. **Scope every query explicitly.** `for_workspace()` only filters when called;
   every new view must resolve the workspace from the URL, check membership with the
   decorators in `apps.members.decorators`, and have a cross-client access test.
6. **Never commit secrets, client data or client files.** `.env`, `media/` and
   `backups/` are ignored by git and by Docker.

## Remotes

```bash
git remote -v
# origin    https://github.com/juliemangoes/brown-creative-studio (the public fork: push here)
# upstream  https://github.com/brightbeanxyz/brightbean-studio (fetch only)
```

The `upstream` push URL is set to an invalid value on purpose so nothing is pushed
to BrightBean by accident.

## Merging upstream

Do this regularly (monthly, and before each phase) so the gap stays small:

```bash
git fetch upstream
git switch -c merge/upstream-$(date +%Y-%m-%d) main
git merge upstream/main
```

Then:

1. Resolve conflicts. Each conflicting upstream file should already be listed in
   `CHANGES.md`; re-apply the recorded change on top of upstream's new version.
2. Run `python manage.py apply_design_tokens` to bring new or changed upstream
   templates onto the design tokens, and review its report.
3. Run the whole suite as CI does (ruff, ruff format, mypy, `makemigrations --check`, pytest).
4. Read upstream's new migrations and settings for anything that touches the fork
   (new defaults, renamed settings, new templates with their own `<html>` shell; the
   source-link test catches the last one).
5. Add a `CHANGES.md` entry for the merge: upstream commit range, conflicts, and any
   test expectation that changed and why.
6. Open a pull request into `main`; merge after CI passes.
