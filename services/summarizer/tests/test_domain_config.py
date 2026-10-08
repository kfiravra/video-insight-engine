"""Tests for domain_config.py — ensures domains.json is valid and consistent."""

import json
from pathlib import Path

import pytest

# Load domains.json directly for testing
DOMAINS_JSON_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "packages"
    / "shared"
    / "src"
    / "config"
    / "domains.json"
)


@pytest.fixture
def config():
    """Load the raw domain config."""
    return json.loads(DOMAINS_JSON_PATH.read_text())


class TestDomainsJsonStructure:
    """Validate the structure of domains.json."""

    def test_has_required_top_level_keys(self, config):
        assert "domains" in config
        assert "modifiers" in config
        assert "categoryMap" in config
        assert "components" in config
        assert "quizEnrichment" in config

    def test_has_expected_domains(self, config):
        expected = {
            "learning",
            "tech",
            "fitness",
            "food",
            "music",
            "travel",
            "review",
            "project",
            "language",
            "science",
            "podcast",
            "news",
            "gaming",
            "sport",
        }
        assert set(config["domains"].keys()) == expected

    def test_has_expected_modifiers(self, config):
        expected = {"narrative", "finance"}
        assert set(config["modifiers"].keys()) == expected

    def test_domain_has_required_fields(self, config):
        for name, domain in config["domains"].items():
            assert "emoji" in domain, f"{name} missing emoji"
            assert "label" in domain, f"{name} missing label"
            assert "gradient" in domain, f"{name} missing gradient"
            assert "defaultTabs" in domain, f"{name} missing defaultTabs"

    def test_modifier_has_required_fields(self, config):
        for name, modifier in config["modifiers"].items():
            assert "emoji" in modifier, f"{name} missing emoji"
            assert "label" in modifier, f"{name} missing label"

    def test_default_tabs_are_complete_objects(self, config):
        """Each defaultTab should be a complete object with id, label, emoji, component."""
        for domain_name, domain in config["domains"].items():
            for tab in domain["defaultTabs"]:
                assert isinstance(tab, dict), f"{domain_name} defaultTab is not a dict: {tab}"
                assert "id" in tab, f"{domain_name} tab missing id: {tab}"
                assert "label" in tab, f"{domain_name} tab missing label: {tab}"
                assert "emoji" in tab, f"{domain_name} tab missing emoji: {tab}"
                assert "component" in tab, f"{domain_name} tab missing component: {tab}"

    def test_components_array_valid(self, config):
        """Components array should contain non-empty strings."""
        components = config["components"]
        assert isinstance(components, list)
        assert len(components) > 0
        for c in components:
            assert isinstance(c, str) and len(c) > 0


class TestDomainsJsonConsistency:
    """Ensure internal consistency of domains.json."""

    def test_category_map_points_to_valid_domains(self, config):
        valid_domains = set(config["domains"].keys())
        for category, tag in config["categoryMap"].items():
            assert tag in valid_domains, (
                f"categoryMap['{category}'] = '{tag}' is not a valid domain. Valid: {valid_domains}"
            )

    def test_gradients_are_css_gradients(self, config):
        for name, domain in config["domains"].items():
            assert domain["gradient"].startswith("linear-gradient("), (
                f"{name} gradient doesn't start with 'linear-gradient(': {domain['gradient']}"
            )

    def test_at_least_one_default_tab(self, config):
        for name, domain in config["domains"].items():
            assert len(domain["defaultTabs"]) >= 1, f"{name} has no default tabs"

    def test_default_tab_components_in_components_array(self, config):
        """Each defaultTab component should be in the top-level components array."""
        valid_components = set(config["components"])
        for domain_name, domain in config["domains"].items():
            for tab in domain["defaultTabs"]:
                component = tab.get("component", "")
                assert component in valid_components, (
                    f"{domain_name}.{tab['id']} component '{component}' not in components array"
                )


class TestDomainConfigModule:
    """Test the Python domain_config module functions."""

    def test_valid_content_tags_matches_json(self, config):
        from src.shared_config.domain_config import valid_content_tags

        assert valid_content_tags() == frozenset(config["domains"].keys())

    def test_valid_modifiers_matches_json(self, config):
        from src.shared_config.domain_config import valid_modifiers

        assert valid_modifiers() == frozenset(config["modifiers"].keys())

    def test_valid_components(self, config):
        from src.shared_config.domain_config import valid_components

        result = valid_components()
        assert isinstance(result, frozenset)
        assert result == frozenset(config["components"])
        assert "spot_explorer" in result
        assert "overview" in result

    def test_map_category_to_tag(self):
        from src.shared_config.domain_config import map_category_to_tag

        assert map_category_to_tag("cooking") == "food"
        assert map_category_to_tag("coding") == "tech"
        assert map_category_to_tag("unknown_category") == "learning"

    def test_get_default_tab_ids(self):
        from src.shared_config.domain_config import get_default_tab_ids

        ids = get_default_tab_ids("learning")
        assert "key_points" in ids
        assert "concepts" in ids

    def test_get_tab_meta(self):
        from src.shared_config.domain_config import get_tab_meta

        meta = get_tab_meta("key_points")
        assert meta is not None
        label, emoji = meta
        assert label == "Key Points"
        assert emoji != ""

    def test_get_tab_meta_unknown(self):
        from src.shared_config.domain_config import get_tab_meta

        assert get_tab_meta("nonexistent_tab") is None

    def test_build_fallback_tabs(self):
        from src.shared_config.domain_config import build_fallback_tabs

        tabs = build_fallback_tabs("tech")
        assert len(tabs) > 0
        assert all("id" in t and "label" in t and "emoji" in t and "component" in t for t in tabs)

    def test_build_fallback_tabs_have_component(self):
        from src.shared_config.domain_config import build_fallback_tabs

        tabs = build_fallback_tabs("travel")
        assert len(tabs) > 0
        for tab in tabs:
            assert "component" in tab
            assert tab["component"] != ""


class TestSiblingDataSources:
    """Registry helpers that power the assembly in-domain sibling fallback."""

    def test_datasources_for_component_priority_order(self):
        from src.shared_config.domain_config import datasources_for_component

        # tech lists `code` (tech.snippets) before `patterns` (tech.patterns),
        # both backing code_playground — defaultTabs order IS the priority.
        assert datasources_for_component("tech", "code_playground") == [
            "tech.snippets",
            "tech.patterns",
        ]

    def test_datasources_for_component_unknown_returns_empty(self):
        from src.shared_config.domain_config import datasources_for_component

        assert datasources_for_component("tech", "no_such_component") == []
        assert datasources_for_component("no_such_domain", "code_playground") == []

    def test_sibling_datasources_excludes_self(self):
        from src.shared_config.domain_config import sibling_datasources

        assert sibling_datasources("tech", "tech.patterns") == ["tech.snippets"]
        assert sibling_datasources("tech", "tech.snippets") == ["tech.patterns"]

    def test_sibling_datasources_unregistered_field_returns_empty(self):
        from src.shared_config.domain_config import sibling_datasources

        # tech.topics is a valid extraction field but NOT a registered defaultTab.
        assert sibling_datasources("tech", "tech.topics") == []


class TestEffectiveRequirements:
    """Merged layout policy: domain requirements + (domain, format) playbook."""

    def test_forbidden_is_union_of_domain_and_playbook(self):
        from src.shared_config.domain_config import effective_requirements

        merged = effective_requirements("gaming", "unboxing")
        assert "quiz_arena" in merged["forbidden"]
        assert "code_playground" in merged["forbidden"]  # playbook-only entry

    def test_playbook_required_overrides_domain(self):
        from src.shared_config.domain_config import effective_requirements

        merged = effective_requirements("review", "unboxing")
        # review domain requires comparison; the unboxing playbook overrides to []
        assert merged["required"] == []

    def test_missing_playbook_falls_back_to_domain_values(self):
        from src.shared_config.domain_config import effective_requirements

        merged = effective_requirements("gaming", "commentary")
        assert merged["required"] == ["tier_list"]
        assert merged["forbidden"] == frozenset({"quiz_arena"})
        assert merged["planGuidance"] == ""

    def test_none_format_falls_back_to_domain_values(self):
        from src.shared_config.domain_config import effective_requirements

        merged = effective_requirements("travel", None)
        assert merged["required"] == ["spot_explorer"]
        assert "quiz_arena" in merged["forbidden"]

    def test_educational_domains_allow_quiz(self):
        from src.shared_config.domain_config import effective_requirements

        for domain in ("learning", "language", "tech", "science"):
            merged = effective_requirements(domain, None)
            assert "quiz_arena" not in merged["forbidden"], domain
            assert merged["max"].get("quiz_arena") == 1, domain

    def test_non_educational_domains_forbid_quiz(self, config):
        from src.shared_config.domain_config import effective_requirements

        educational = {"learning", "language", "tech", "science"}
        for domain in config["domainRequirements"]:
            if domain in educational:
                continue
            merged = effective_requirements(domain, None)
            assert "quiz_arena" in merged["forbidden"], domain
            assert "quiz_arena" not in merged["max"], domain

    def test_playbook_components_are_known(self, config):
        valid = set(config["components"])
        for key, playbook in config.get("playbooks", {}).items():
            for field in ("required", "preferred", "forbidden"):
                for comp in playbook.get(field, []):
                    assert comp in valid, f"{key}.{field}: unknown component {comp}"

    def test_playbook_keys_reference_valid_domains(self, config):
        for key in config.get("playbooks", {}):
            domain = key.split(":", 1)[0]
            assert domain in config["domains"], key


class TestVisualCriticalityConfig:
    def test_config_shape(self):
        from src.shared_config.domain_config import visual_criticality_config

        cfg = visual_criticality_config()
        assert set(cfg["tiers"].keys()) == {"high", "standard", "low"}
        assert cfg["tiers"]["high"]["overselect"] > cfg["tiers"]["high"]["keep"]
        assert cfg["tiers"]["low"]["visionMax"] == 0

    def test_high_low_domains_are_valid(self, config):
        domains = set(config["domains"].keys())
        vc = config["visualCriticality"]
        assert set(vc["highDomains"]) <= domains
        assert set(vc["lowDomains"]) <= domains
        assert not set(vc["highDomains"]) & set(vc["lowDomains"])


class TestQuizEnrichmentConfig:
    def test_every_quiz_domain_is_a_known_domain(self, config):
        assert set(config["quizEnrichment"]["quizDomains"]) <= set(config["domains"])

    def test_quiz_prompt_exists_on_disk(self):
        prompts_dir = Path(__file__).resolve().parent.parent / "src" / "prompts"
        assert (prompts_dir / "enrich_quiz.txt").exists()
