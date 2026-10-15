"""Unit tests for density enforcement + info_grid string normalization (1B)."""

from src.services.pipeline.assembly import assemble_info_grid, enforce_density


class TestEnforceDensity:
    def test_caps_long_info_grid_value_in_table_mode(self):
        props = {"mode": "table", "items": [{"key": "Feature", "value": "x" * 200}]}
        out = enforce_density("info_grid", props)
        assert out is not None
        assert len(out["items"][0]["value"]) == 120
        assert out["items"][0]["value"].endswith("…")

    def test_caps_long_info_grid_key_in_table_mode(self):
        props = {"mode": "table", "items": [{"key": "k" * 200, "value": "short"}]}
        out = enforce_density("info_grid", props)
        assert len(out["items"][0]["key"]) == 120

    def test_short_cells_untouched(self):
        props = {"items": [{"key": "Display", "value": "6.7 inch"}]}
        out = enforce_density("info_grid", props)
        assert out["items"][0]["value"] == "6.7 inch"

    def test_unknown_component_passes_through(self):
        props = {"steps": [{"instruction": "x" * 300}]}
        out = enforce_density("step_player", props)
        assert out is props

    def test_caps_evidence_field_in_tag_cloud_mode(self):
        props = {"mode": "tag_cloud", "items": [{"key": "K", "value": "v", "evidence": "e" * 200}]}
        out = enforce_density("info_grid", props)
        assert len(out["items"][0]["evidence"]) == 120


class TestInfoGridStringNormalization:
    def test_em_dash_string_splits_into_pair(self):
        result = assemble_info_grid({}, ["ReLU — Rectified Linear Unit"], {}, None)
        assert result is not None
        assert result["items"][0] == {"key": "ReLU", "value": "Rectified Linear Unit"}

    def test_colon_string_splits_into_pair(self):
        result = assemble_info_grid({}, ["Battery: 5000mAh"], {}, None)
        assert result["items"][0] == {"key": "Battery", "value": "5000mAh"}

    def test_long_delimiterless_string_is_kept_as_a_headline_chip(self):
        # 0b.4: a long sentence with nothing to split on is no longer dropped —
        # the frontend renders any key length in full (S12 lost 4/7 takeaways).
        paragraph = ("This is a long explanatory sentence " * 5).strip()
        result = assemble_info_grid({}, [paragraph], {}, None)
        assert result["items"][0] == {"key": paragraph, "value": ""}

    def test_short_label_survives_as_key_chip(self):
        result = assemble_info_grid({}, ["Waterproof"], {}, None)
        assert result["items"][0] == {"key": "Waterproof", "value": ""}

    def test_mixed_list_keeps_pairs_and_paragraphs(self):
        paragraph = "A long delimiterless explanatory sentence about nothing " * 4
        result = assemble_info_grid({}, ["Speed: fast", paragraph], {}, None)
        assert result is not None
        assert len(result["items"]) == 2
        assert result["items"][0]["key"] == "Speed"
