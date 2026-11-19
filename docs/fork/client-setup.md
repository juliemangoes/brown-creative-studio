# Client setup: onboarding, projects, brand

Phase 3 adds three apps, all scoped to a client workspace like everything else:

- `apps.client_onboarding`: one guided onboarding per client, built from templates.
- `apps.projects`: projects and tasks, with templates, four views, workload and retainer delivery.
- `apps.brand`: the brand profile, brand stages signed off by the client, brand files, and the Brand Hub.

## Onboarding

**Starting.** On a client's **Onboarding** tab, choose the services this onboarding covers
and the contacts to invite, then start. Each invited contact gets a portal account with no
password (they sign in from the emailed link) and a role: the first is *Client admin*, others
*Client reviewer*. The client moves from Lead or Proposal sent to Onboarding.

**The flow.** One guided flow in the portal, adapted to the services bought:

| # | Step | Done by | Where the answer goes |
|---|---|---|---|
| 1 | Welcome and timeline | Client | |
| 2 | Proposal | Studio confirms | Phase 5 connects proposals |
| 3 | Contract | Studio confirms | Phase 5 connects e-signature |
| 4 | Deposit or first invoice | Studio confirms | Phase 5 connects invoices |
| 5 | Business and billing details | Client | The client record and contacts |
| 6 | Questionnaire (per service) | Client, several people | Answers, some into the brand profile |
| 7 | Logos, fonts, photos, past materials | Client | Brand files; photos into the media library |
| 8 | Connect social accounts (social media only) | Client | BrightBean's connection link |
| 9 | Kickoff booking | Client | A milestone in the first project |

Everything saves as people go; several people on the client's side can work on it, and each
answer records who gave it. Steps 2 to 4 are marked done by the studio until billing and
e-signature exist (Phase 5); the client sees what happens next and any note the studio adds.

**Following progress.** The Onboarding tab shows each step, blockers (internal notes, never
shown to the client), who has and hasn't responded, the reminder schedule and the kickoff.
Today lists onboarding that has gone quiet for `STUDIO_ONBOARDING_IDLE_DAYS` (5) or run longer
than `STUDIO_ONBOARDING_STALL_DAYS` (14).

**Reminders.** Sent automatically by the worker (hourly task `send_onboarding_reminders`) on
the days after the start listed in the schedule (default 2, 5, 9, 14), to every participant,
only while the client still has steps left. Editable per client and per template.

**Completion.** When every step is done or skipped (or the studio clicks *Complete now*):

1. The brand profile is created or filled in from the answers (only empty fields).
2. A project per service, with its starter tasks: *Monthly social retainer* for this month (the
   first planning cycle: plan, approval, shoot, edit, approval, scheduling, report), *Brand
   project* for branding, or *Brand strategy* for strategy only.
3. The booked kickoff becomes a milestone.
4. The client moves to Active. Everything is in the audit log.

**Templates.** *Onboarding templates* (from a client's Onboarding tab or the project
templates page): an "Every client" template (welcome, timeline, reminders, base steps and
questions) and one per service line (extra steps and questions). People with
`manage_templates` edit them; onboardings already running keep their questions.

## Projects and tasks

- **Projects** in the rail: every task across the clients you work on, as a **list**,
  **board** (drag between columns, or use each card's Move menu), **timeline** (8 weeks, bars from
  start to due date) or **calendar** (by due date). Filters for status, person, client, due date,
  project type and milestones; save any combination as a named filter.
- **A client's projects**: open projects with progress, finished ones, and **retainer
  delivery**: posts published per month against the plan, counted once across platforms. Reels,
  stories and shoot days show the plan only, until Phase 4 records post formats and shoots.
- **A project**: milestones (what the client sees), progress, the internal brief, and the four
  views for its tasks. Tasks have an owner, dates, dependencies ("waits on"), a checklist, team
  comments and links to files.
- **Workload**: open tasks per person per week, by due date, with overdue and undated columns.
- **Templates**: Monthly social retainer, Content shoot, Campaign, Brand project, Brand
  strategy. Edit tasks, timing, milestones, checklists and dependencies.
- **Freelancers** (contributor role) see and update only the tasks assigned to them. Viewers see
  tasks but can't change them. Managers and editors manage projects.
- **Portal → Projects**: each project's milestones and progress; never task notes, assignees
  or comments. A project can be hidden from the portal.

## Brand

On a client's **Brand** tab:

- **Profile**: strategy (purpose, positioning, values, personality, audiences, competitors),
  voice and messaging, colours (with HEX and RGB), fonts, and content likes and dislikes.
  Internal notes stay internal. `BrandProfile.as_brief()` gives content planning and AI
  drafting (Phases 4 and 8) the same brand, without the internal notes.
- **Stages and sign-off**: the seven stages of the brand project (four for strategy), each
  ending with the client's sign-off in the Brand Hub. Sending a stage emails client admins and
  reviewers. Signing off completes the project milestone and starts the next stage. Asking for
  changes on Identity design counts a revision round against the contract's limit.
- **Files**: logos by format, fonts, templates, guidelines, and what the client uploaded.
  Studio files reach the client only when shared.

**Brand Hub** in the portal shows, once shared: the stages and anything waiting for sign-off,
strategy, logos with a download per format, colours with copyable values, fonts, voice and
messaging, and templates and examples.

## Private files

Brand files and onboarding uploads are client data:

- Identified by their bytes, not their name (a `.png` must really be a PNG); SVG, EPS/AI, fonts
  and ZIP-based office files are accepted for brand work. 50 MB per file.
- **Scanned for malware** before they are stored (`apps/studio/scanning.py`). Production should
  run ClamAV: `docker compose ... --profile scan up -d` and `MALWARE_SCAN_BACKEND=clamd`. The
  default `mock` scanner only recognises the EICAR test file, and the deploy check `about.W008`
  warns until ClamAV is on. Uploads fail closed: infected files, and files arriving while the
  scanner is unreachable, are refused.
- **Stored privately**: on S3 under `private/` (private objects); on local storage in
  `PRIVATE_MEDIA_ROOT`, never served by Caddy or Django's media route.
- **Downloaded through signed links** that expire after `FILE_LINK_TTL` seconds (default 300).
  The download view checks again that the signed-in person may see that client's file; on S3
  it then redirects to a presigned URL valid for one minute. Pages link to a small view that
  issues a fresh link on each click, so links on an open page don't go stale.

## Not tested against real services

- **ClamAV**: the clamd protocol is implemented and tested against a stand-in server in the
  tests; it could not be run against a real ClamAV here because the sandbox blocks virus
  definition downloads.
- **Social account connection** (step 8) uses BrightBean's connection flow unchanged; it needs
  each platform's app credentials (Phase 6) and was not exercised against real platforms.
- **Email** (invitations, reminders, sign-off requests) is sent through Django's email backend;
  tests check the messages, not delivery.
