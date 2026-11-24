# Publishing

How approved posts go out to Facebook, Instagram and TikTok, what happens when something goes
wrong, and how TikTok is posted by hand until TikTok approves the app.

## The publishing queue

**Publishing** in the left rail (needs `view_plan`). For one client it shows, in the client's
time; for all clients, in studio time:

| Group | What it means |
|---|---|
| **Manual action needed** | Due, and someone has to post it by hand (TikTok). "Post it" opens the page with the video, caption and the field for the live link. |
| **Failed** | It did not go out. The reason is in plain words. **Fix** offers **Try again now** or **It is live: paste its link**. |
| **Publishing** | Sent to the platform; waiting for it to finish. |
| **Scheduled** | Approved and waiting for its time. |
| **Published** | Out, with a **View the live post** link where the platform gave one (last 14 days). |
| **Before it goes out** | Posts due in the next 48 hours that have a problem (below). |

Today shows **Post by hand** (open manual posts) and **Won't go out as it stands** (problems in the
next 24 hours). Only Facebook, Instagram and TikTok can be connected; the other BrightBean
platforms are hidden (an admin can show them again under Platform visibility).

## Approved posts publish once

BrightBean's engine does the publishing (queue, retries with backoff, rate limits, confirmation,
publish log). The studio adds:

- A post only reaches the queue with the approvals the client's workflow requires, for that exact
  content (Phase 4). Editing it afterwards sends it back for review.
- **Uncertain outcomes are never retried automatically.** Facebook and Instagram take no
  "idempotency key", so when the connection drops, times out or the platform answers with a server
  error *while publishing*, the post may or may not be live. Instead of trying again (and maybe
  posting twice), the post fails with: "We lost contact with Instagram while publishing, so this
  post may or may not be live. Check the page before trying again." The same applies when the
  worker stopped mid-publish.
- **Safe retry.** **Try again now** works only on a failed post, only if the account is connected,
  the media is still valid and the approval still covers it. For an uncertain failure it also
  needs the box "I checked Instagram: this post is not live". No other route (composer, calendar,
  API, MCP) can put such a post back in the queue without that check.
- **It is live: paste its link** marks a failed post Published with its link, without posting.
- A clean refusal (for example a 400 for an unsupported image) is retried by the engine as before,
  and a post that keeps failing stops after the retry budget.
- The live post's ID and link are stored on the post.
- Every failure notifies the post's author and the studio owners. Retries, posts marked live and
  posts completed by hand are in the audit log.
- Recurring background work (checks, reminders, billing, analytics) runs in the worker's general
  lane; the publishing lane is reserved for publishing and confirmation, so it never waits behind
  them.

## Pre-publish checks

Every 15 minutes the app checks each post due in the next 48 hours (`PUBLISHING_CHECK_HOURS`):

- **Connection:** the account is disconnected, or its token expires before the post's time.
- **Approval:** not approved yet, approved but not scheduled, or changed after approval.
- **Media:** a platform that needs a video or an image has none; a file failed or is still
  processing.
- **By hand:** TikTok posts are noted as posted by hand.

Once a problem is within 24 hours (`PUBLISHING_ALERT_HOURS`), the post's author (or the person
posting it) and the studio owners get one notification for it. Moving the post to another time
re-arms the alert.

## TikTok: posted by hand (assisted posting)

TikTok's Direct Post API is only for apps TikTok has audited. Until then
`TIKTOK_DIRECT_POST_ENABLED=false` (the default) and **nothing is sent to TikTok's API**:

1. An hour before its time (`PUBLISHING_MANUAL_LEAD_MINUTES`), an approved, scheduled TikTok post
   becomes **Manual action needed** and a task opens for the post's author (or, if they no longer
   work on that client, the studio owner). They get a notification.
2. The task page has the video to download, the caption (and first comment) to copy, the due time
   and who it is assigned to. Anyone who can publish can reassign it.
3. They post it from the client's TikTok account and paste the live link. The app checks it is a
   TikTok link, stores it and the video ID, and marks the post **Published**. Pasting again does
   nothing: a task completes once.
4. If it is an hour late (`PUBLISHING_MANUAL_REMINDER_MINUTES`), they get one reminder.
5. If the post is edited and sent back for review, the task is cancelled.

When Direct Post is switched on and TikTok puts an upload in the creator's inbox (TikTok's "send to
inbox" result), the post becomes **Manual action needed** too, not Failed: someone opens TikTok,
finishes the post from the inbox and pastes the link.

## Setting up Meta (Facebook Pages and Instagram)

Nothing in this phase has been tested against Meta's real API: it needs the studio's Meta app.

1. **Meta app.** At developers.facebook.com create a *Business* app owned by the studio's Business
   portfolio. Add **Facebook Login for Business** (and, if you prefer, a configuration for it).
2. **Business verification** of the studio's Business portfolio (Business settings → Security
   center). Meta requires it before App Review for these permissions.
3. **Permissions** the app requests (BrightBean's providers):
   - Facebook Pages: `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`,
     `pages_read_user_content`, `pages_manage_engagement`, `pages_manage_metadata`,
     `business_management`, `pages_messaging` (and `read_insights` for analytics).
   - Instagram (business or creator account linked to a Page): `instagram_basic`,
     `instagram_content_publish`, `instagram_manage_comments`, `instagram_manage_insights`,
     `instagram_manage_messages` (turn off with `PLATFORM_INSTAGRAM_FACEBOOK_DM_SCOPES_ENABLED=false`
     if not approved), `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`.
4. **App Review** for each permission, with a screencast of the studio connecting a Page and
   publishing. Until it is approved, only people with a role on the app (admins, developers,
   testers) can connect, which is enough to test with the studio's own Page and Instagram.
5. **Redirect URL:** `https://<your domain>/social-accounts/callback/facebook/` and
   `.../callback/instagram/` under Facebook Login → Valid OAuth redirect URIs.
6. **Environment:** `PLATFORM_FACEBOOK_APP_ID`, `PLATFORM_FACEBOOK_APP_SECRET`
   (`PLATFORM_FACEBOOK_CONFIG_ID` optional), and the same app for Instagram via Facebook Login.
   Never commit them; set them on the server only.

### How Instagram publishing works

Instagram publishes in two steps: the app creates a *container* from the media URL, waits for
Instagram to process it, then publishes the container. The engine creates the container when the
post is due (not earlier), so the media link it hands Instagram is fresh.

- **Media links must be reachable by Instagram.** With S3 storage, each file is given a private,
  signed link that expires after an hour (`AWS_QUERYSTRING_EXPIRE`). Do not set `S3_CUSTOM_DOMAIN`
  to a domain that serves unsigned links to private files; with local storage, `APP_URL` must be
  public and HTTPS.
- **Publishing limit.** Before publishing, the app reads the account's live limit
  (`content_publishing_limit`) instead of assuming a number. If it is used up, nothing is sent: the post
  is retried on the engine's normal schedule and fails with a plain reason if the limit is still
  used up after the last retry. If the check itself fails, publishing goes ahead (Instagram
  enforces the limit anyway).

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `TIKTOK_DIRECT_POST_ENABLED` | `false` | `true` only once TikTok has approved Direct Post for the app. |
| `PUBLISHING_MANUAL_LEAD_MINUTES` | `60` | How early a hand-posted post becomes Manual action needed. |
| `PUBLISHING_MANUAL_REMINDER_MINUTES` | `60` | When a late hand-posted post gets its reminder. |
| `PUBLISHING_CHECK_HOURS` | `48` | How far ahead the checks look. |
| `PUBLISHING_ALERT_HOURS` | `24` | How close a problem must be before people are alerted. |

## Trying it

```bash
python manage.py seed_demo --reset
```

As the demo owner open **Publishing** → Luma Skincare: a TikTok post waiting to be posted by hand
(open it, download, copy, paste `https://www.tiktok.com/@lumaskincare/video/123` to finish), an
Instagram post whose outcome is unknown (**Fix** asks you to confirm it is not live), and published
posts with their links. Don't run the publishing worker against the demo accounts: they have no
tokens.

## Not tested against real services

- **Meta (Facebook Pages, Instagram):** publishing, the publishing-limit check and token expiry
  have run only against mocked responses. They need the studio's Meta app, App Review and Business
  verification.
- **TikTok Direct Post and the inbox result:** switched off; tested with mocks only.
- **Assisted posting** does not touch TikTok at all and is fully tested.
