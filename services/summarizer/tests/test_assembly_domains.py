"""Full-domain assembly tests — podcast, news, gaming, sport."""

from src.services.pipeline.assembly import (
    assemble_response,
)

# ─── resolve_data_source ───


class TestPodcastAssembly:
    def _triage(self):
        return {
            "contentTags": ["podcast"],
            "modifiers": [],
            "primaryTag": "podcast",
            "userGoal": "Follow the conversation",
            "tabs": [
                {
                    "id": "segments",
                    "label": "Segments",
                    "emoji": "🎙️",
                    "component": "moment_track",
                    "dataSource": "podcast.segments",
                },
                {
                    "id": "guests",
                    "label": "Guests",
                    "emoji": "🧑",
                    "component": "spot_explorer",
                    "dataSource": "podcast.guests",
                },
                {
                    "id": "quotes",
                    "label": "Quotes",
                    "emoji": "💬",
                    "component": "flash_deck",
                    "dataSource": "podcast.quotes",
                },
                {
                    "id": "topics",
                    "label": "Topics",
                    "emoji": "🗂️",
                    "component": "info_grid",
                    "dataSource": "podcast.topics",
                },
            ],
        }

    def test_default_tabs_assemble(self):
        extraction = {
            "podcast": {
                "segments": [
                    {"title": "Intro", "summary": "Welcome", "timestamp": 0},
                    {"title": "Deep dive", "summary": "Consensus", "timestamp": 480},
                    {"title": "Wrap", "summary": "Closing", "timestamp": 900},
                ],
                "guests": [
                    {"name": "Dr. Cho", "role": "Engineer", "description": "Distributed systems"},
                    {"name": "Sam R", "role": "Host", "description": "Runs the show"},
                ],
                "quotes": [
                    {"quote": "You can't beat physics", "speaker": "Cho"},
                    {"quote": "Latency is forever", "speaker": "Cho"},
                    {"quote": "Ship it", "speaker": "Sam"},
                ],
                "topics": [
                    {"topic": "CAP theorem", "detail": "Consistency vs availability"},
                    {"topic": "Raft", "detail": "Understandable consensus"},
                    {"topic": "Latency", "detail": "Physics limits"},
                ],
            },
        }
        out = assemble_response(self._triage(), extraction, None, None)
        components = [t["component"] for t in out["tabs"]]
        assert "moment_track" in components
        assert "spot_explorer" in components
        assert out["meta"]["primaryTag"] == "podcast"
        # Segments are the required spine.
        seg = next(t for t in out["tabs"] if t["component"] == "moment_track")
        assert len(seg["props"]["items"]) == 3


class TestNewsAssembly:
    def _triage(self):
        return {
            "contentTags": ["news"],
            "modifiers": [],
            "primaryTag": "news",
            "userGoal": "Understand the story",
            "tabs": [
                {
                    "id": "timeline",
                    "label": "Timeline",
                    "emoji": "🕐",
                    "component": "moment_track",
                    "dataSource": "news.storyTimeline",
                },
                {
                    "id": "entities",
                    "label": "People",
                    "emoji": "👤",
                    "component": "spot_explorer",
                    "dataSource": "news.entities",
                },
                {
                    "id": "claims",
                    "label": "Claims",
                    "emoji": "🔎",
                    "component": "claims_tracker",
                    "dataSource": "news.claims",
                },
                {
                    "id": "context",
                    "label": "Context",
                    "emoji": "🗂️",
                    "component": "info_grid",
                    "dataSource": "news.context",
                },
            ],
        }

    def test_claims_tracker_and_timeline_assemble(self):
        extraction = {
            "news": {
                "storyTimeline": [
                    {"label": "Proposed", "description": "Budget submitted", "timestamp": 0},
                    {"label": "Hearing", "description": "Public split", "timestamp": 320},
                    {"label": "Vote", "description": "Approved 6-3", "timestamp": 740},
                ],
                "entities": [
                    {"name": "Mayor Diaz", "role": "Sponsor", "description": "Pushed plan"},
                    {
                        "name": "Transit Authority",
                        "role": "Implementer",
                        "description": "Runs transit",
                    },
                ],
                "claims": [
                    {
                        "claim": "Creates 1200 jobs",
                        "source": "Mayor",
                        "status": "disputed",
                        "sourceCitation": "Analysts say 700",
                        "timestamp": 210,
                    },
                    {
                        "claim": "Fares rise 15%",
                        "source": "Authority",
                        "status": "verified",
                        "timestamp": 540,
                    },
                    {"claim": "Structural deficit", "source": "Reporter", "status": "context"},
                ],
                "context": [
                    {"key": "Annual budget", "value": "$3.4B"},
                    {"key": "Last increase", "value": "2021"},
                    {"key": "Population", "value": "1.2M"},
                ],
            },
        }
        out = assemble_response(self._triage(), extraction, None, None)
        components = [t["component"] for t in out["tabs"]]
        assert "claims_tracker" in components
        assert "moment_track" in components
        assert out["meta"]["primaryTag"] == "news"
        claims_tab = next(t for t in out["tabs"] if t["component"] == "claims_tracker")
        assert len(claims_tab["props"]["claims"]) == 3
        statuses = {c["status"] for c in claims_tab["props"]["claims"]}
        assert statuses == {"disputed", "verified", "context"}


class TestGamingAssembly:
    def _triage(self):
        return {
            "contentTags": ["gaming"],
            "modifiers": [],
            "primaryTag": "gaming",
            "userGoal": "Improve at the game",
            "tabs": [
                {
                    "id": "highlights",
                    "label": "Highlights",
                    "emoji": "🎬",
                    "component": "moment_track",
                    "dataSource": "gaming.highlights",
                },
                {
                    "id": "loadout",
                    "label": "Loadout",
                    "emoji": "🎒",
                    "component": "checklist",
                    "dataSource": "gaming.loadout",
                },
                {
                    "id": "tier_list",
                    "label": "Tier List",
                    "emoji": "🏆",
                    "component": "tier_list",
                    "dataSource": "gaming.rankings",
                },
            ],
        }

    def test_default_tabs_assemble(self):
        extraction = {
            "gaming": {
                "highlights": [
                    {"label": "Clutch", "description": "1v3", "timestamp": 95},
                    {"label": "Ace", "description": "Five kills", "timestamp": 410},
                    {"label": "Whiff", "description": "Missed", "timestamp": 720},
                ],
                "loadout": [
                    {"item": "Vandal", "category": "weapons", "note": "One-tap"},
                    {"item": "Light shields", "category": "economy", "note": "Eco"},
                    {"item": "0.4 sens", "category": "settings", "note": "Mouse"},
                ],
                # 0b.13: a tier is a stated rank only on an anchored row.
                "rankings": [
                    {"item": "Jett", "tier": "S", "reason": "Entry", "timestamp": 30},
                    {"item": "Sage", "tier": "A", "reason": "Heal", "timestamp": 60},
                    {"item": "Yoru", "tier": "C", "reason": "Niche", "timestamp": 90},
                ],
            },
        }
        out = assemble_response(self._triage(), extraction, None, None)
        components = [t["component"] for t in out["tabs"]]
        assert "tier_list" in components
        assert "moment_track" in components
        assert out["meta"]["primaryTag"] == "gaming"
        tier_tab = next(t for t in out["tabs"] if t["component"] == "tier_list")
        assert len(tier_tab["props"]["items"]) == 3


class TestSportAssembly:
    def _triage(self):
        return {
            "contentTags": ["sport"],
            "modifiers": [],
            "primaryTag": "sport",
            "userGoal": "Understand the match",
            "tabs": [
                {
                    "id": "match_events",
                    "label": "Events",
                    "emoji": "⏱️",
                    "component": "moment_track",
                    "dataSource": "sport.matchEvents",
                },
                {
                    "id": "formation",
                    "label": "Formation",
                    "emoji": "📋",
                    "component": "formation_diagram",
                    "dataSource": "sport.formation",
                },
            ],
        }

    def test_default_tabs_assemble(self):
        extraction = {
            "sport": {
                "matchEvents": [
                    {"label": "Goal", "description": "Tap-in", "timestamp": 720},
                    {"label": "Equaliser", "description": "Header", "timestamp": 1980},
                    {"label": "Winner", "description": "Top corner", "timestamp": 4980},
                ],
                "formation": {
                    "name": "4-3-3",
                    "team": "City",
                    "positions": [
                        {"player": "Ederson", "role": "GK", "x": 50, "y": 8, "number": 31},
                        {"player": "Rodri", "role": "CDM", "x": 50, "y": 50, "number": 16},
                        {"player": "Haaland", "role": "ST", "x": 50, "y": 90, "number": 9},
                    ],
                },
            },
        }
        out = assemble_response(self._triage(), extraction, None, None)
        components = [t["component"] for t in out["tabs"]]
        assert "formation_diagram" in components
        assert "moment_track" in components
        assert out["meta"]["primaryTag"] == "sport"
        formation_tab = next(t for t in out["tabs"] if t["component"] == "formation_diagram")
        assert len(formation_tab["props"]["positions"]) == 3
        assert formation_tab["props"]["name"] == "4-3-3"


class TestForbiddenBackstop:
    """Assembly-level forbidden pop — the backstop behind plan-time policy."""

    def test_forbidden_component_popped_with_reason(self):
        from src.services.pipeline.assembly.core import _validate_domain_requirements

        tabs = [
            {
                "id": "pulls",
                "component": "tier_list",
                "label": "Pulls",
                "goal": "g",
                "props": {"items": [{"item": "x"}]},
            },
            {
                "id": "quiz",
                "component": "quiz_arena",
                "label": "Quiz",
                "goal": "g",
                "props": {"questions": [{"q": "?"}]},
            },
        ]
        dropped: list[dict] = []
        _validate_domain_requirements(
            tabs, "gaming", dropped_sink=dropped, content_format="unboxing"
        )

        assert [t["id"] for t in tabs if t["component"] == "quiz_arena"] == []
        assert any(d["reason"] == "domain_forbidden" and d["id"] == "quiz" for d in dropped)

    def test_educational_domain_keeps_quiz_tab(self):
        from src.services.pipeline.assembly.core import _validate_domain_requirements

        tabs = [
            {
                "id": "quiz",
                "component": "quiz_arena",
                "label": "Quiz",
                "goal": "g",
                "props": {"questions": [{"q": "?"}]},
            },
        ]
        dropped: list[dict] = []
        _validate_domain_requirements(tabs, "learning", dropped_sink=dropped, content_format=None)

        assert [t["id"] for t in tabs] == ["quiz"]
        assert dropped == []
