# Content operations

How a post goes from idea to shoot to client approval, and where each step lives.

## The path of one post

1. **Idea.** Ideas live in the idea bank (Ideas in the rail). On the client's **Plan**, the
   idea bank panel turns an idea into a planned post in one action: a dated draft on the
   client's connected accounts, with a content plan. No media yet means the stage starts at
   *Shot needed*.
2. **Plan.** `Calendar → Plan` (or **Plan** on the client header) shows 13 weeks by week:
   each post with its production stage and approval status ("Shot needed · Draft"), key dates
   and campaigns. *To watch* lists gaps against the retainer, one pillar or format taking over
   the next month, posts within 7 days with no photo or video, posts within 3 days not
   approved, and media whose rights end before the post. A post's page holds the brief:
   objective, format, hook, script, call to action, hashtags, owner, campaign and dates.
3. **Shoot.** `Shoots` lists every content day. A shoot has date and times, locations, crew,
   equipment, talent (add "(signed)" to a line once that person's release is in), run of show,
   wardrobe, props, permits, client preparation and notes. Booking conflicts (a person, a piece
   of equipment or a location on another shoot at the same time, across clients) show on the
   shoot and on the list. **Planned posts that need a shot** adds them to the shot list, each
   shot tied to its post and given a code (S01, S02…).
4. **Call sheet.** The client gets a page and a PDF (share link, turn it off or renew it any
   time) and an email with the preparation checklist. Internal notes are never on either.
5. **On set.** *On-set mode* is built for a phone: tick shots (Got it / Retake / Skip), add
   notes and capture unplanned ideas. It keeps working without signal: changes queue on the
   phone and are sent when the connection returns; sending twice does no harm.
6. **Material.** Upload files on the shoot. They are scanned, stored in the client's media
   library with the shoot recorded as their source, and matched to shots by code in the file
   name (`S03_tacos.jpg`, `casa-s3.mov`). A matched file marks the shot captured and becomes
   the post's media if it has none. Unmatched files can be matched by hand. **Mark done and create tasks for missing shots** marks
   the shoot done and turns missing must-have shots into follow-up tasks.
7. **Review and approval.** Submitting moves the stage to *Ready for review*. The studio
   reviews first; then the client approves in the portal (one post, or a week or month at
   once), commenting with a pin on an image or a video time. Only a client can approve the
   client stage. Each decision records the exact version approved.
8. **History.** The post's review page (**Approval and full history**) lists everything in
   order: the idea, planning, stage changes, shots added and captured, matched files, every
   version, every review decision, comments and publishing. Internal comments are marked and
   never shown to clients.

## Approval rules that always hold

- A post cannot be scheduled unless the required approvals cover its exact current content.
  This is checked on every route: the composer, the calendar (drag and bulk publish), the API
  and any code that saves a platform post.
- Changing approved content (text, tags, media or a media file, per-platform overrides) sends
  it back to studio review and records why.
- An owner or manager with the *override approval* permission can approve on the client's
  behalf, with a written reason; it is recorded as an override in the audit log and history.
- **Approvers and turnaround** (the client's workspace settings) name who approves at each stage and the
  turnaround in hours, which sets the due times shown on Today.
- **Portal links** (the client's workspace settings) can be revoked one at a time or all at once.

## Asset rights

Every media asset has **Rights and use** (from the media library): source, usage (organic and
paid, organic only, internal only), licence terms, release reference, and the date the rights
end. The page lists every post and shot that uses the asset and warns about posts going out
after the rights end; the Plan warns too.

## Key dates

`Plan → Key dates` holds client dates and studio-wide dates. **Add Belize holidays** adds the
public holidays for this year and next. Some holidays are observed on a nearby Monday: check
the official list each year before relying on the exact day off.

## Today

Today adds **Shoot days, next 2 weeks** with what is still missing for each (no shot list,
must-have shots not tied to a post, no crew, no location, release forms outstanding, client
prep not sent, booking conflicts), and **Posts that need a shoot**: posts marked *Shot needed*
in the next three weeks that no upcoming shoot covers.

## Who sees what

| | Owner, manager | Editor | Contributor (freelancer) | Viewer | Client |
|---|---|---|---|---|---|
| Plan, campaigns, key dates, post history | edit | edit | no | see | no |
| Shoots | plan | plan | only shoots they are crew on; tick shots | see | call sheet link only |
| Asset rights | edit | edit | no | see | no |
| Approvals | studio stage, override | studio stage | no | no | client stage |

Seeing the plan is the `view_plan` permission; changing it also needs `create_posts`. Shoots
use `view_shoots` and `manage_shoots`, asset rights `edit_media`, overrides `approve_posts`
plus the studio-wide *override approval* permission. Internal comments in a post's history
need `view_internal`. Booking conflicts with another client's shoot are shown without that
client's name or shoot title to anyone who doesn't work on that client.

Every page checks the client in the URL against the person's membership; a post, shoot or
asset from another client returns "not found".

## Trying it

```bash
python manage.py seed_demo --reset
```

Sign in as the demo owner and open Casa Maya Restaurant. *Mole negro, step by step* went from
an idea to the client's approval over the last week (Plan → the post → Approval and full
history). *December menu shoot* is next week, not ready yet, and clashes with a Luma shoot.
*Rosa, twenty years at the stove* uses a licensed photo whose rights end first.

## Not tested against real services

- **Email** (call sheet and preparation email, portal emails): rendered and sent through
  Django's mail backend in tests; delivery through a real provider is not tested here.
- **Malware scanning** of uploads runs the mock scanner in tests; ClamAV itself was not
  reachable from the build environment.
- **Publishing** after approval uses BrightBean's publisher; demo accounts are placeholders.
- **Offline mode** is tested at the sync endpoint (idempotent replays) and the page loads its
  script and service worker; it has not been tried on a physical phone in airplane mode.
