"""The studio's starter project templates. Created once per studio; the studio can edit them after.

Brand and strategy templates have one sign-off milestone per brand stage
(``<stage>_signoff``); ``apps.brand`` links its stages to them.
"""

from apps.projects.models import ProjectKind


def t(key, title, offset, duration, *, milestone=False, assign="owner", checklist=(), depends=(), description=""):
    return {
        "key": key,
        "title": title,
        "offset_days": offset,
        "duration_days": duration,
        "milestone": milestone,
        "assign": assign,
        "checklist": list(checklist),
        "depends_on": list(depends),
        "description": description,
    }


SOCIAL_RETAINER = [
    t(
        "plan",
        "Draft the month's content plan",
        0,
        3,
        checklist=[
            "Review last month's results",
            "Check holidays and the client's key dates",
            "Balance content pillars and formats",
            "List what needs to be shot",
        ],
    ),
    t("plan_approval", "Content plan approved by the client", 3, 2, milestone=True, depends=["plan"]),
    t(
        "shoot_prep",
        "Prepare the shoot",
        5,
        3,
        depends=["plan_approval"],
        checklist=[
            "Shot list",
            "Location and time confirmed",
            "Props and wardrobe",
            "Call sheet sent",
        ],
    ),
    t("shoot", "Shoot day", 9, 1, milestone=True, depends=["shoot_prep"]),
    t("edit", "Edit photos and videos", 10, 4, assign="", depends=["shoot"]),
    t("captions", "Write captions and hashtags", 10, 4, assign="", depends=["plan_approval"]),
    t("internal_review", "Internal review", 14, 1, depends=["edit", "captions"]),
    t("client_approval", "Posts sent for client approval", 15, 2, milestone=True, depends=["internal_review"]),
    t("schedule", "Schedule approved posts", 17, 1, depends=["client_approval"]),
    t("community", "Community check-ins", 0, 28, assign=""),
    t("report", "Monthly report shared", 27, 3, milestone=True),
]

SHOOT = [
    t("date", "Confirm date, time and location", 0, 2),
    t(
        "shot_list",
        "Write the shot list",
        1,
        3,
        checklist=["Must-have shots", "Formats (photo, Reel, story)", "Matched to planned posts"],
    ),
    t("call_sheet", "Send the call sheet", 4, 1, depends=["date", "shot_list"]),
    t("props", "Props, wardrobe and styling", 3, 3, assign=""),
    t("shoot", "Shoot day", 7, 1, milestone=True, depends=["call_sheet"]),
    t("backup", "Back up and cull", 8, 1, depends=["shoot"]),
    t("edit", "Edit the selects", 9, 3, assign="", depends=["backup"]),
    t("deliver", "Files delivered to the library", 12, 1, milestone=True, depends=["edit"]),
]

CAMPAIGN = [
    t(
        "brief",
        "Campaign brief and goals",
        0,
        3,
        checklist=["Objective and measure of success", "Audience", "Budget and boosting"],
    ),
    t("concept", "Concept and key message", 3, 4, depends=["brief"]),
    t("plan_approval", "Campaign plan approved by the client", 7, 2, milestone=True, depends=["concept"]),
    t("produce", "Produce the assets", 9, 7, assign="", depends=["plan_approval"]),
    t("content_approval", "Campaign content approved", 16, 2, milestone=True, depends=["produce"]),
    t("launch", "Launch", 18, 1, milestone=True, depends=["content_approval"]),
    t("check", "Mid-campaign check", 25, 1),
    t("wrap", "Wrap-up report shared", 32, 3, milestone=True),
]

# Brand stages, in order: (key, title, tasks)
BRAND_STAGES = [
    ("discovery", "Discovery", ["Review the questionnaire", "Stakeholder interviews", "Agree business goals"]),
    ("research", "Research", ["Brand audit", "Competitor review", "Market and audience research"]),
    (
        "strategy",
        "Strategy",
        ["Positioning and purpose", "Values and personality", "Audience profiles", "Messaging framework"],
    ),
    ("verbal", "Verbal identity", ["Naming (if needed)", "Voice and tone", "Tagline and key messages"]),
    ("visual", "Visual direction", ["Moodboards", "Creative territories"]),
    ("identity", "Identity design", ["Logo concepts", "Visual system", "Revision rounds"]),
    ("guidelines", "Guidelines and handoff", ["Brand guidelines", "Final file export", "Launch assets"]),
]
STAGE_DAYS = {
    "discovery": 10,
    "research": 10,
    "strategy": 10,
    "verbal": 7,
    "visual": 7,
    "identity": 14,
    "guidelines": 7,
}


def brand_tasks(stage_keys):
    tasks, offset, previous = [], 0, None
    for key, title, steps in BRAND_STAGES:
        if key not in stage_keys:
            continue
        days = STAGE_DAYS[key]
        step_keys = []
        for i, step in enumerate(steps):
            step_key = f"{key}_{i}"
            step_keys.append(step_key)
            tasks.append(
                t(
                    step_key,
                    step,
                    offset,
                    max(days - 2, 1),
                    assign="" if i else "owner",
                    depends=[f"{previous}_signoff"] if previous else [],
                )
            )
        tasks.append(
            t(
                f"{key}_signoff",
                f"{title} signed off by the client",
                offset + days - 2,
                2,
                milestone=True,
                depends=step_keys,
            )
        )
        offset += days
        previous = key
    return tasks


ALL_BRAND_STAGES = [key for key, _, _ in BRAND_STAGES]
STRATEGY_STAGES = ["discovery", "research", "strategy", "verbal"]

BUILTINS = {
    "social_retainer": (
        "Monthly social retainer",
        ProjectKind.SOCIAL_RETAINER,
        "One month of planning, shooting, approvals, publishing and reporting.",
        SOCIAL_RETAINER,
    ),
    "shoot": ("Content shoot", ProjectKind.SHOOT, "From shot list to delivered files.", SHOOT),
    "campaign": ("Campaign", ProjectKind.CAMPAIGN, "Brief, concept, approval, launch and wrap-up.", CAMPAIGN),
    "brand": (
        "Brand project",
        ProjectKind.BRAND,
        "Seven stages, each ending in a client sign-off.",
        brand_tasks(ALL_BRAND_STAGES),
    ),
    "strategy": (
        "Brand strategy",
        ProjectKind.STRATEGY,
        "Discovery to verbal identity, each stage signed off by the client.",
        brand_tasks(STRATEGY_STAGES),
    ),
}
