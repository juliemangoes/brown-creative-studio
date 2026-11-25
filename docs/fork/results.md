# Results and reports

Per-client dashboards, goals, monthly reports (with an AI-drafted summary the studio edits and
approves), sharing in the portal or by link, PDF export, and the portal's Results section.

## Where things are

- **Reports** in the left rail: for one client, its **Results** (Dashboard, Goals, Monthly reports,
  Import figures, and BrightBean's per-account analytics); for all clients, every report with those
  waiting for approval first.
- **Today** lists reports waiting for approval (for people who can approve them).
- **Client health** gains a **Goals** signal: how many goals are behind this period.
- **Portal → Results**: published work with its figures, and the reports the studio shared.

## Every figure says what it is

Each figure has a "What this means" panel: its definition, the platform field it comes from, and
when that data was last updated. The rules (from the brief, checked 18 Nov 2026):

- **Not available is never zero.** A metric a platform does not give says *Not available from this
  platform* (e.g. unique viewers on TikTok, saves on Facebook). One the platform gives but we do not
  read yet says *Not collected yet* (profile activity and video retention on Instagram and Facebook,
  followers on TikTok).
- **Totals across platforms are labelled *Combined*.** Views on Instagram and Facebook are added
  together but marked as a total of metrics each platform counts its own way. **Unique viewers are
  never added** across platforms (someone who saw two accounts would count twice); when only one
  platform has them, the figure says "Instagram only."
- **Calculated figures are labelled.** TikTok's per-video counts are lifetime totals, so daily TikTok
  figures are the studio's calculation from day-to-day differences; Instagram audience growth is
  calculated from the follower total. Both carry *Calculated*.
- **Instagram builds on views**, never impressions or plays (deprecated).
- **Facebook unique viewers** come from `page_total_media_view_unique`, which replaced Page reach on
  15 June 2026. A period that spans the change shows a note and **no comparison**, so the two are
  never one line.
- **Comparisons need data.** The previous period of the same length is compared only when it has
  data on at least 80% of its days; otherwise "No comparison: there is too little data for the
  previous period" (so an account connected mid-month doesn't read as 300% growth).
- **Imported figures** (CSV, below) fill only days with no platform data and are labelled
  *Imported*.

| Metric | Instagram | Facebook | TikTok |
|---|---|---|---|
| Audience growth | change in `followers_count` (calculated) | `page_daily_follows_unique` | Not collected yet |
| Views | `views` | `page_media_view` | `view_count` (calculated daily) |
| Unique viewers | `reach` | `page_total_media_view_unique` | Not available |
| Engagement | likes + comments + shares + saved | reactions + comments + shares | likes + comments + shares (calculated daily) |
| Engagement rate | engagement / views | engagement / views | engagement / views |
| Comments, shares | ✓ | ✓ | ✓ (calculated daily) |
| Saves | `saved` | Not available | Not collected yet |
| Link clicks | Not available | `post_clicks` | Not available |
| Profile activity | Not collected yet | Not collected yet | Not available |
| Video retention | Not collected yet | Not collected yet | Not available |

## The dashboard

Choose the platform (each one, or all) and the period (last 7, 30 or 90 days, this month, last
month); every figure is compared with the previous period of the same length. Below: daily views,
**top content** ranked by the metric you choose (views, engagement, engagement rate, shares, saves,
comments), the month's goals, and **comparisons by pillar, format, hook, length and posting time**
(average per post, with how many posts each; groups under 3 posts are marked "few").

Post figures are each post's lifetime totals so far (BrightBean's `PostInsightsSnapshot`), so a post
published last week has had less time than one from a month ago.

## Goals

**Results → Goals** (needs `manage_reports`): a metric (audience growth, views, unique viewers,
engagement, engagement rate, shares, saves, link clicks, comments), a platform or all, a target, and
either every month or between two dates. Status: **Met**, **On track** (counts: at least 90% of the
pace for the days gone; rates: at the target level), **Behind**, or **No data yet**.

## Monthly reports

1. **Start** one for a month (Results → Monthly reports), choosing what ranks the top posts. Its
   figures come from the snapshots at that moment; **Update the figures** refreshes them while it is
   a draft.
2. **Draft with AI** writes the three sections the client reads: *What happened*, *Why it likely
   happened*, *What we'll do next*, in the client's portal language. The writer gets only the
   report's own figures (never internal notes) and is told not to invent any number, source or
   trend; any number in the draft that isn't in the figures is listed as "Check this figure". Edit
   freely. Choose posts to feature, or leave it to the ranking.
3. **Internal notes** are for the studio only: never in the portal, the link or the PDF (tested).
4. **Send for approval** (needs a written summary). People with `approve_reports` get a notification
   and see it on Today.
5. **Approve** (owners and managers by default): the figures are frozen, so the client always sees
   what was approved. **Back to draft** is possible until it is shared.
6. **Share in the client's portal**: client users with `portal_view_reports` are emailed in their
   language and find it under Results. Or **Create a link** for people without a login (read-only,
   rate-limited, can be turned off).
7. **Download PDF** at any stage; it shows exactly what the portal shows.

Every step is in the audit log.

## AI drafting

`AI_PROVIDER`:

| Value | What it does |
|---|---|
| `mock` (default) | Writes the draft from the figures with fixed sentences. No service is called. The editor says it was written by the test assistant. |
| `claude` | Anthropic's Messages API (`AI_MODEL`, default `claude-opus-5-5`), key in `ANTHROPIC_API_KEY` (server environment only). JSON output with the three sections; server-side fallback on a refusal; a refusal or error becomes a plain message and the summary can be written by hand. **Not tested against the live API.** |
| `none` | Drafting is off. |

The AI never approves, shares or publishes anything. BrightBean's hosted Intelligence add-on stays
off (its variables stay blank).

## Importing figures (CSV)

**Results → Import figures** (needs `manage_reports`), for TikTok's own exports or months before an
account was connected. A UTF-8 CSV with `date,metric,value` (dates `YYYY-MM-DD`); metrics `views`,
`engagement`, `comments`, `shares`, `saves`, `audience_growth`, `unique_viewers`, `link_clicks`. One
value per account, metric and day (a new import replaces it); rows that don't parse are skipped and
counted.

## Permissions

| Key | Who by default | Allows |
|---|---|---|
| `view_analytics` (BrightBean) | owner, manager, editor, viewer | Dashboard, goals, reports, PDFs |
| `manage_reports` | owner, manager, editor | Goals, writing reports, CSV import |
| `approve_reports` | owner, manager | Approving, sharing, share links |
| `portal_view_reports` | Client admin preset | Portal Results |

## Trying it

```bash
python manage.py seed_demo --reset
```

- Casa Maya → Reports: the dashboard (try *Last month* and Instagram), three goals, and October's
  report, **shared** (with internal notes the client never sees) and with a share link.
- Luma → Reports: October's report **waiting for approval** (also on Today), drafted by the test
  assistant. Approve it, share it, download the PDF.
- Portal as Ana (Casa Maya, Spanish): Resultados, with the gallery and the shared report.

## Not tested against real services

- **Live platform figures:** the dashboard reads BrightBean's daily snapshots, which need connected
  accounts and Meta/TikTok app review; the demo uses made-up figures.
- **Claude drafting:** tested with a mocked API client only.
- **Email delivery** of the "report ready" message (tests use Django's mail backend).
