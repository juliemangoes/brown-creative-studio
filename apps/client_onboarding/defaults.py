"""Starter onboarding templates: one for every client, and one per service line.

A client buying several services gets one flow: the base template's welcome,
timeline and reminders, every service's steps, and every service's questions
(each question once). ``maps_to`` sends an answer into the brand profile.
"""


def q(key, label, kind="long", *, help="", required=False, choices=(), maps_to=""):
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "help": help,
        "required": required,
        "choices": list(choices),
        "maps_to": maps_to,
    }


BASE_STEPS = ["welcome", "proposal", "contract", "deposit", "details", "questionnaire", "uploads", "kickoff"]

WELCOME = (
    "Welcome! This is where we get everything we need to start. It takes about 20 minutes, "
    "you can do it in parts (everything saves as you go), and anyone on your team we've invited can help."
)

BUILTINS = {
    "base": {
        "name": "Every client",
        "service_kind": "",
        "welcome_message": WELCOME,
        "timeline": [
            {"label": "Onboarding", "when": "Week 1"},
            {"label": "Kickoff meeting", "when": "Week 1 to 2"},
        ],
        "steps": BASE_STEPS,
        "reminder_days": [2, 5, 9, 14],
        "questions": [
            q("goals", "What do you want to achieve with us in the next six months?", required=True),
            q(
                "audience",
                "Who are your customers? Describe one or two kinds of people you want to reach.",
                required=True,
                maps_to="audiences",
            ),
        ],
    },
    "social_media": {
        "name": "Social media management",
        "service_kind": "social_media",
        "timeline": [
            {"label": "First month's content plan", "when": "Week 2 to 3"},
            {"label": "First posts go live", "when": "Week 4"},
        ],
        "steps": ["accounts"],
        "questions": [
            q(
                "tone",
                "How should you sound on social media?",
                "choice",
                choices=["Warm and friendly", "Playful", "Polished and premium", "Straightforward and practical"],
                maps_to="voice",
            ),
            q("content_likes", "Show us accounts or posts you like, and tell us why.", maps_to="content_likes"),
            q("content_avoid", "Anything we should never post or say?", maps_to="content_avoid"),
            q("highlights", "Which products, dishes or services should we feature first?"),
            q("key_dates", "Key dates coming up: launches, holidays, events, busy and quiet seasons."),
            q(
                "competitors",
                "Which businesses or accounts do you see as competitors?",
                "list",
                help="One per line.",
                maps_to="competitors",
            ),
            q("approvals", "Who approves posts on your side, and how quickly can they usually reply?", "text"),
        ],
    },
    "branding": {
        "name": "Branding",
        "service_kind": "branding",
        "timeline": [
            {"label": "Discovery and research", "when": "Weeks 2 to 5"},
            {"label": "Strategy and verbal identity", "when": "Weeks 5 to 8"},
            {"label": "Visual direction and identity design", "when": "Weeks 8 to 11"},
            {"label": "Guidelines and handoff", "when": "Week 12"},
        ],
        "steps": [],
        "questions": [
            q("story", "How did the business start, and why does it exist?", required=True, maps_to="purpose"),
            q("keep_change", "What do you want to keep from your current brand, and what should change?"),
            q(
                "personality",
                "Three to five words that describe your brand's personality.",
                "list",
                help="One per line.",
                maps_to="personality",
            ),
            q("inspiration", "Brands you admire, in any industry, and why."),
            q("avoid", "Colours, styles or ideas to avoid."),
            q(
                "uses",
                "Where will the brand be used? Signage, packaging, menus, uniforms, website…",
                "list",
                help="One per line.",
            ),
        ],
    },
    "brand_strategy": {
        "name": "Brand strategy",
        "service_kind": "brand_strategy",
        "timeline": [
            {"label": "Discovery and stakeholder interviews", "when": "Weeks 2 to 3"},
            {"label": "Research", "when": "Weeks 3 to 5"},
            {"label": "Strategy and messaging", "when": "Weeks 5 to 8"},
        ],
        "steps": [],
        "questions": [
            q("business_goals", "Your business goals for the next one to three years.", required=True),
            q("differentiators", "What makes you different from your competitors?", maps_to="positioning"),
            q("values", "What do you stand for? List your values.", "list", help="One per line.", maps_to="values"),
            q("stakeholders", "Who should we interview? Names and roles.", "list", help="One per line."),
            q(
                "competitors",
                "Which businesses do you see as competitors?",
                "list",
                help="One per line.",
                maps_to="competitors",
            ),
        ],
    },
}

# What each answer type fills in BrandProfile, when that field is still empty.
TEXT_TARGETS = {"purpose", "positioning", "voice", "content_likes", "content_avoid", "elevator_pitch"}
LIST_TARGETS = {"values", "personality", "competitors", "key_messages", "voice_do", "voice_dont"}

PLATFORM_HELP = [
    (
        "Instagram",
        "Use a Business or Creator account linked to a Facebook Page. You'll sign in with Facebook and choose the Page and Instagram account to give us.",
    ),
    ("Facebook", "Sign in with a Facebook profile that is an admin of the business Page, then choose the Page."),
    (
        "TikTok",
        "Sign in to the TikTok account itself. Some posts may still need a final tap in the TikTok app; we'll tell you when.",
    ),
    ("LinkedIn", "Sign in with a LinkedIn profile that is an admin of the company page."),
    ("YouTube", "Sign in with the Google account that owns or manages the channel."),
]
