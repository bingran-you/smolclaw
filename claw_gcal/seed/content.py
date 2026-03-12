"""Curated seed content for Calendar scenarios.

This mirrors the Gmail seed architecture: static templates + deterministic
randomization hooks consumed by scenario seeders.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------
PERSONAS: dict[str, dict[str, str]] = {
    "sarah": {
        "name": "Sarah Kim",
        "email": "sarah.kim@nexusai.com",
        "role": "manager",
    },
    "marcus": {
        "name": "Marcus Rivera",
        "email": "marcus.rivera@nexusai.com",
        "role": "engineer",
    },
    "priya": {
        "name": "Priya Sharma",
        "email": "priya.sharma@nexusai.com",
        "role": "engineer",
    },
    "james": {
        "name": "James Liu",
        "email": "james.liu@nexusai.com",
        "role": "engineer",
    },
    "lisa": {
        "name": "Lisa Wang",
        "email": "lisa.wang@nexusai.com",
        "role": "operations",
    },
    "amy": {
        "name": "Amy Chen",
        "email": "amy.chen@gmail.com",
        "role": "friend",
    },
    "david": {
        "name": "David Park",
        "email": "david.park@gmail.com",
        "role": "friend",
    },
    "vendor": {
        "name": "Nina Foster",
        "email": "nina@vendorco.com",
        "role": "vendor",
    },
}

# ---------------------------------------------------------------------------
# Calendar templates
# ---------------------------------------------------------------------------
CALENDAR_TEMPLATES: list[dict] = [
    {
        "key": "primary",
        "summary": "{email}",
        "description": "Primary calendar",
        "timezone": "UTC",
        "accessRole": "owner",
        "primary": True,
        "selected": True,
        "hidden": False,
        "colorId": "14",
    },
    {
        "key": "team",
        "summary": "NexusAI Engineering",
        "description": "Team rituals and engineering meetings",
        "timezone": "UTC",
        "accessRole": "owner",
        "primary": False,
        "selected": True,
        "hidden": False,
        "colorId": "9",
        "acl_rules": [
            {"scopeType": "default", "role": "reader"},
        ],
    },
    {
        "key": "product",
        "summary": "Product Launch Calendar",
        "description": "Product, GTM, and customer milestones",
        "timezone": "UTC",
        "accessRole": "owner",
        "primary": False,
        "selected": True,
        "hidden": False,
        "colorId": "10",
    },
    {
        "key": "travel",
        "summary": "Travel",
        "description": "Flights, hotels, and conference travel",
        "timezone": "UTC",
        "accessRole": "owner",
        "primary": False,
        "selected": False,
        "hidden": False,
        "colorId": "11",
    },
    {
        "key": "family",
        "summary": "Family",
        "description": "Personal and family commitments",
        "timezone": "UTC",
        "accessRole": "owner",
        "primary": False,
        "selected": False,
        "hidden": False,
        "colorId": "12",
    },
    {
        "key": "holidays",
        "summary": "US Holidays",
        "description": "US federal holidays",
        "timezone": "UTC",
        "accessRole": "reader",
        "primary": False,
        "selected": True,
        "hidden": False,
        "colorId": "5",
        "acl_rules": [
            {"scopeType": "default", "role": "reader"},
        ],
    },
]

# ---------------------------------------------------------------------------
# Fixed needles (phase 1)
# ---------------------------------------------------------------------------
NEEDLE_EVENTS: list[dict] = [
    {
        "summary": "Q2 Planning Kickoff",
        "calendar": "team",
        "description": "Finalize engineering priorities for Q2.",
        "location": "Zoom",
        "days_from_now": 2,
        "start_hour": 10,
        "duration_hours": 2,
        "attendees": ["sarah", "marcus", "priya", "james"],
    },
    {
        "summary": "Vendor Contract Renewal Deadline",
        "calendar": "product",
        "description": "Finalize renewal terms with VendorCo before EOD.",
        "location": "HQ Board Room",
        "days_from_now": 5,
        "start_hour": 16,
        "duration_hours": 1,
        "attendees": ["vendor", "sarah"],
    },
    {
        "summary": "Flight SFO -> JFK",
        "calendar": "travel",
        "description": "Conference travel to New York.",
        "location": "SFO Terminal 3",
        "days_from_now": 7,
        "start_hour": 8,
        "duration_hours": 6,
    },
    {
        "summary": "Postmortem: Checkout Incident",
        "calendar": "product",
        "description": "Review root cause and follow-up actions.",
        "location": "War Room",
        "days_from_now": -3,
        "start_hour": 14,
        "duration_hours": 1.5,
        "attendees": ["marcus", "priya", "james", "lisa"],
    },
    {
        "summary": "Family Dinner",
        "calendar": "family",
        "description": "Dinner reservation with family.",
        "location": "North Beach",
        "days_from_now": 1,
        "start_hour": 19,
        "duration_hours": 2,
    },
    {
        "summary": "Quarterly OKR Review",
        "calendar": "primary",
        "description": "Review progress against quarterly OKRs.",
        "location": "Home Office",
        "days_from_now": -1,
        "start_hour": 11,
        "duration_hours": 1,
        "attendees": ["sarah"],
    },
    {
        "summary": "Canceled: Architecture Sync",
        "calendar": "team",
        "description": "This sync was canceled after async update.",
        "location": "Zoom",
        "days_from_now": -5,
        "start_hour": 15,
        "duration_hours": 1,
        "status": "cancelled",
    },
    {
        "summary": "Thanksgiving Day",
        "calendar": "holidays",
        "description": "US federal holiday",
        "location": "",
        "days_from_now": 30,
        "all_day": True,
        "duration_days": 1,
    },
]

RECURRING_NEEDLES: list[dict] = [
    {
        "summary": "Daily Standup",
        "calendar": "team",
        "description": "Daily engineering standup.",
        "location": "Zoom",
        "days_from_now": -2,
        "start_hour": 9,
        "duration_hours": 0.5,
        "recurrence": ["RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;COUNT=20"],
        "attendees": ["sarah", "marcus", "priya", "james"],
    },
    {
        "summary": "Weekly 1:1 with Sarah",
        "calendar": "primary",
        "description": "Career growth and unblockers.",
        "location": "HQ 5F",
        "days_from_now": 0,
        "start_hour": 16,
        "duration_hours": 0.5,
        "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=12"],
        "attendees": ["sarah"],
    },
    {
        "summary": "Gym Session",
        "calendar": "primary",
        "description": "Personal fitness routine.",
        "location": "Gym",
        "days_from_now": -1,
        "start_hour": 7,
        "duration_hours": 1,
        "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=18"],
    },
]

# ---------------------------------------------------------------------------
# Template pools (phase 2 fill)
# ---------------------------------------------------------------------------
WORK_EVENT_POOL: list[dict] = [
    {
        "summary": "{project}: sprint planning",
        "calendar": "team",
        "description": "Plan backlog and dependencies for {project}.",
        "location": "{room}",
        "days_from_now_range": (-21, 45),
        "start_hour_choices": [9, 10, 11, 14, 15],
        "duration_hours_choices": [1, 2],
        "attendees_pool": ["sarah", "marcus", "priya", "james"],
        "attendees_count_range": (2, 4),
    },
    {
        "summary": "Design review: {topic}",
        "calendar": "product",
        "description": "Cross-functional design review for {topic}.",
        "location": "{room}",
        "days_from_now_range": (-30, 50),
        "start_hour_choices": [10, 13, 15],
        "duration_hours_choices": [1, 1.5],
        "attendees_pool": ["sarah", "marcus", "priya", "lisa"],
        "attendees_count_range": (2, 3),
    },
]

OPS_EVENT_POOL: list[dict] = [
    {
        "summary": "Incident drill: {project}",
        "calendar": "product",
        "description": "Run failover and rollback drill for {project}.",
        "location": "War Room",
        "days_from_now_range": (-40, 35),
        "start_hour_choices": [10, 14, 16],
        "duration_hours_choices": [1, 2],
        "attendees_pool": ["marcus", "priya", "james", "lisa"],
        "attendees_count_range": (2, 4),
        "cancelled_ratio": 0.08,
    },
    {
        "summary": "On-call handoff",
        "calendar": "team",
        "description": "Weekly handoff with incident context and pending risks.",
        "location": "Slack Huddle",
        "days_from_now_range": (-60, 30),
        "start_hour_choices": [8, 9, 17],
        "duration_hours_choices": [0.5, 1],
        "attendees_pool": ["marcus", "priya", "james"],
        "attendees_count_range": (1, 2),
    },
]

PERSONAL_EVENT_POOL: list[dict] = [
    {
        "summary": "Coffee with {persona}",
        "calendar": "family",
        "description": "Catch up and life updates.",
        "location": "{city}",
        "days_from_now_range": (-14, 30),
        "start_hour_choices": [8, 9, 17, 18],
        "duration_hours_choices": [1, 1.5],
        "attendees_pool": ["amy", "david"],
        "attendees_count_range": (1, 1),
    },
    {
        "summary": "Deep work: {topic}",
        "calendar": "primary",
        "description": "Focus block for strategic work.",
        "location": "Home Office",
        "days_from_now_range": (-20, 40),
        "start_hour_choices": [7, 8, 13],
        "duration_hours_choices": [1, 2, 3],
    },
]

TRAVEL_EVENT_POOL: list[dict] = [
    {
        "summary": "Travel: {city}",
        "calendar": "travel",
        "description": "Flight and hotel itinerary for {city}.",
        "location": "Airport",
        "days_from_now_range": (-90, 90),
        "start_hour_choices": [6, 7, 8, 9, 20],
        "duration_hours_choices": [2, 4, 6],
    },
    {
        "summary": "Conference day: {topic}",
        "calendar": "travel",
        "description": "Conference sessions and meetings.",
        "location": "Convention Center",
        "days_from_now_range": (-90, 90),
        "all_day": True,
        "duration_days": 1,
    },
]

EVENT_POOLS: dict[str, list[dict]] = {
    "work": WORK_EVENT_POOL,
    "ops": OPS_EVENT_POOL,
    "personal": PERSONAL_EVENT_POOL,
    "travel": TRAVEL_EVENT_POOL,
}

# ---------------------------------------------------------------------------
# Distribution configs
# ---------------------------------------------------------------------------
DEFAULT_TARGET_EVENTS = 72
DEFAULT_DISTRIBUTION: dict[str, float] = {
    "work": 0.45,
    "ops": 0.20,
    "personal": 0.25,
    "travel": 0.10,
}

LONG_CONTEXT_TARGET_EVENTS = 1400
LONG_CONTEXT_DISTRIBUTION: dict[str, float] = {
    "work": 0.40,
    "ops": 0.25,
    "personal": 0.20,
    "travel": 0.15,
}

# Placeholder pools for template parameterization.
PROJECT_NAMES = [
    "Agent Runtime",
    "Retrieval Service",
    "Billing API",
    "Observability Stack",
    "Calendar Sync",
    "Inference Gateway",
    "Onboarding Flow",
]

MEETING_TOPICS = [
    "launch readiness",
    "schema migration",
    "latency budget",
    "auth hardening",
    "cost controls",
    "incident response",
    "customer feedback",
]

CITY_NAMES = [
    "San Francisco",
    "New York",
    "Seattle",
    "Austin",
    "Boston",
    "Chicago",
]

ROOM_NAMES = [
    "Zoom",
    "HQ-5F Saturn",
    "HQ-4F Jupiter",
    "War Room",
    "Meet Room A",
]
