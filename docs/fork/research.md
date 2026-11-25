# Research

A research profile, a swipe file and a weekly brief for each client. **Every recommendation shows
what was observed, its source, date and sample size, or it is labelled a hypothesis to test.** The
app enforces this: a recommendation saved without evidence is always a hypothesis.

## Where things are

**Research** in the left rail (needs `view_research`): for one client, the **Weekly brief**, the
**Swipe file** and the **Research profile**; for all clients, this week's brief for each.

## The research profile

Industry, location, language, audience, competitors, reference accounts, keywords, hashtags and
**topics to avoid**. The brief and every AI draft work from it; brand voice and "content to avoid"
come from the Brand Hub. Any suggestion or drafted caption that mentions a topic to avoid is flagged
in red on the recommendation.

## The swipe file

What the team saves by hand: reference posts, hooks, formats, trends, hashtags and sounds, each with
where it was found, when it was seen, what it showed (e.g. "38k views") and when it is likely over.
It feeds "Working in this niche" (posts from competitors and reference accounts) and "Emerging now"
(trends, hashtags, sounds).

**Sources allowed:** official APIs, licensed data providers, web search APIs, and content the team
saves by hand. No scraping, no unofficial APIs. TikTok Creative Center lists trending hashtags and
top ads but has no trends data API: the brief and swipe file link to it, and the team saves what fits
with its date.

## The weekly brief

Prepared every Monday (`RESEARCH_BRIEF_WEEKDAY`, studio time) for clients in Onboarding or Active, by
the background worker's general lane; or now, with **Prepare a new brief**. Three groups:

| Group | From | Evidence shown |
|---|---|---|
| **Working for this client** | The client's published posts in the last 90 days, compared by format, pillar, hook, length and posting time (average engagement per post) | The comparison with post counts on both sides, and the top posts with their dates and figures. Only groups with at least 3 posts each, and only when the best beats the comparison by 15% or more. |
| **Working in this niche** | Swipe-file items from competitors and reference accounts in the last 60 days | The saved examples with account, source, date and what they showed |
| **Emerging now** | Trends, hashtags and sounds saved in the last 21 days, and the trend source if one is configured | The saved item or the source's listing, with its date |

**Confidence** comes from the sample size: high (15 or more), medium (8–14), low (under 8).
Anything without evidence is **Hypothesis to test**: for example, "Test a post around 'brunch'" when
there is no trend data, which says it comes from the profile and not from data. The brief lists what
it could not look at (e.g. "too few published posts to compare").

Each recommendation also has **why it fits**, a **hook to try** and a suggested **format** (AI drafts,
marked as such), and **may stop working by**.

### Acting on a recommendation

- **Plan a post**: a draft post on the calendar for the chosen date, with the suggested format and
  hook in the plan, optionally with **a caption drafted by AI**. It is an ordinary draft: it goes
  through review and approval like any other post. AI never approves, schedules or publishes.
- **Save as an idea**: into BrightBean's idea bank, with the evidence in the description.
- **Dismiss**.

## Learning from results

A week after a post made from a recommendation is published, the app compares its engagement per
post with the **median of the client's posts from the 90 days before** (at least 5): **above** (more
than 10% higher), **about the usual**, or **below**. The result shows on the recommendation, and
**Which advice worked** on the brief counts, for each group and for hypotheses, how many beat the
usual. That is how the studio sees which kinds of advice actually work for each client.

## AI

Uses the same adapter as report drafts (`AI_PROVIDER`: `mock` by default, `claude`, or `none`; see
[results.md](results.md)). The writer receives only the recommendation and its evidence, and the
client's research profile and brand voice (never internal notes). It is told never to invent a
figure, source, trend or result and to say when something is untested. Its text is then checked:
numbers not in the evidence or profile, and topics to avoid, are flagged. With `none`, the brief is
still prepared, without the drafted text.

## Trend sources

`RESEARCH_PROVIDER`:

| Value | What it does |
|---|---|
| `none` (default) | No automatic source: "Emerging now" comes from the swipe file. |
| `mock` | Three made-up trends labelled "TEST", for trying the brief. Never in production. |

A licensed trend provider or a web search API is one more class in `apps/research/sources.py`; every
trend it returns must carry its source, link and date.

## Permissions

| Key | Who by default | Allows |
|---|---|---|
| `view_research` | owner, manager, editor, viewer | Briefs, profile, swipe file |
| `manage_research` | owner, manager, editor | Editing the profile, saving to the swipe file, preparing briefs, acting on recommendations |

Research is studio-only: nothing reaches the portal, the REST API or MCP.

## Trying it

```bash
python manage.py seed_demo --reset
```

Casa Maya → Research: this week's brief (four patterns from its own posts with high confidence, two
from the swipe file, one saved trend), an earlier brief whose recommendation became "Live marimba
Friday" and was judged **above the usual**, the swipe file and the profile. Luma → Research shows a
brief where no group in the client's own posts stood out, so it says so and the recommendations come
from the swipe file.

## Not tested against real services

- **Claude** writing "why it fits", hooks and captions: tested with a mocked API client only.
- **Trend providers:** none is connected; only hand-saved items and the labelled mock exist.
