"""Tests for description analyzer service."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.video.description_analyzer import (
    DescriptionAnalysis,
    DescriptionLink,
    DescriptionTimestamp,
    RelatedVideo,
    Resource,
    SocialLink,
    _analyze_description_async,
    _parse_timestamps,
    analyze_description,
    load_prompt,
)
from src.utils.json_parsing import parse_json_response as _parse_json_response


@pytest.fixture(autouse=True)
def _no_retry_pause():
    """The analysis retries through call_llm_with_retry; never sleep in tests."""
    with patch("src.utils.llm_retry.asyncio.sleep", new=AsyncMock()):
        yield


class TestDescriptionAnalysisDataclass:
    """Tests for DescriptionAnalysis dataclass."""

    def test_default_empty(self):
        """Test default empty analysis."""
        analysis = DescriptionAnalysis()

        assert analysis.links == []
        assert analysis.resources == []
        assert analysis.related_videos == []
        assert analysis.social_links == []
        assert analysis.has_content is False

    def test_has_content_with_links(self):
        """Test has_content returns True when links present."""
        analysis = DescriptionAnalysis(
            links=[DescriptionLink(url="https://example.com", type="github", label="Code")]
        )
        assert analysis.has_content is True

    def test_has_content_with_resources(self):
        """Test has_content returns True when resources present."""
        analysis = DescriptionAnalysis(
            resources=[Resource(name="Tutorial", url="https://example.com")]
        )
        assert analysis.has_content is True

    def test_to_dict(self):
        """Test conversion to dictionary."""
        analysis = DescriptionAnalysis(
            links=[DescriptionLink(url="https://github.com/test", type="github", label="Code")],
            resources=[Resource(name="Tutorial", url="https://example.com")],
            related_videos=[RelatedVideo(title="Part 2", url="https://youtube.com/watch?v=abc")],
            social_links=[SocialLink(platform="twitter", url="https://twitter.com/test")],
        )

        result = analysis.to_dict()

        assert result["links"] == [
            {"url": "https://github.com/test", "type": "github", "label": "Code"}
        ]
        assert result["resources"] == [{"name": "Tutorial", "url": "https://example.com"}]
        assert result["relatedVideos"] == [
            {"title": "Part 2", "url": "https://youtube.com/watch?v=abc"}
        ]
        assert result["socialLinks"] == [{"platform": "twitter", "url": "https://twitter.com/test"}]
        assert result["timestamps"] == []

    def test_has_content_with_timestamps(self):
        """has_content is True when only timestamps are present."""
        analysis = DescriptionAnalysis(
            timestamps=[DescriptionTimestamp(time="2:30", seconds=150, label="Setup")]
        )
        assert analysis.has_content is True

    def test_to_dict_round_trips_timestamps(self):
        analysis = DescriptionAnalysis(
            timestamps=[DescriptionTimestamp(time="1:23:45", seconds=5025, label="Demo")]
        )
        result = analysis.to_dict()
        assert result["timestamps"] == [{"time": "1:23:45", "seconds": 5025, "label": "Demo"}]


class TestParseTimestamps:
    """Tests for _parse_timestamps helper."""

    def test_parses_mm_ss_and_hh_mm_ss(self):
        items = [
            {"time": "2:30", "label": "Setup"},
            {"time": "1:23:45", "label": "Demo"},
        ]
        result = _parse_timestamps(items)

        assert len(result) == 2
        assert result[0] == DescriptionTimestamp(time="2:30", seconds=150, label="Setup")
        assert result[1] == DescriptionTimestamp(time="1:23:45", seconds=5025, label="Demo")

    def test_skips_malformed_time(self):
        result = _parse_timestamps([{"time": "abc", "label": "Bad"}])
        assert result == []

    def test_skips_empty_label(self):
        result = _parse_timestamps([{"time": "0:30", "label": ""}])
        assert result == []

    def test_handles_non_list_input(self):
        assert _parse_timestamps(None) == []
        assert _parse_timestamps("nope") == []


class TestParseJsonResponse:
    """Tests for _parse_json_response function."""

    def test_valid_json(self):
        """Test parsing valid JSON."""
        text = '{"links": [], "resources": []}'
        result = _parse_json_response(text)

        assert result == {"links": [], "resources": []}

    def test_json_with_surrounding_text(self):
        """Test parsing JSON embedded in text."""
        text = 'Here is the response: {"links": [{"url": "test"}]} And some more text.'
        result = _parse_json_response(text)

        assert result == {"links": [{"url": "test"}]}

    def test_invalid_json(self):
        """Test handling invalid JSON."""
        text = '{"links": [invalid json}'
        result = _parse_json_response(text)

        assert result == {}

    def test_no_json_object(self):
        """Test handling text with no JSON object."""
        text = "This is just plain text without any JSON."
        result = _parse_json_response(text)

        assert result == {}

    def test_empty_string(self):
        """Test handling empty string."""
        result = _parse_json_response("")
        assert result == {}

    def test_nested_json(self):
        """Test parsing nested JSON structure."""
        text = '{"links": [{"url": "https://example.com", "type": "github", "label": "Code"}]}'
        result = _parse_json_response(text)

        assert result["links"][0]["url"] == "https://example.com"


class TestAnalyzeDescriptionAsync:
    """Tests for _analyze_description_async function."""

    async def test_short_description_returns_empty(self):
        """Test that very short descriptions return empty analysis."""
        result = await _analyze_description_async("Short")
        assert result.has_content is False

    async def test_empty_description_returns_empty(self):
        """Test that empty description returns empty analysis."""
        result = await _analyze_description_async("")
        assert result.has_content is False

    async def test_whitespace_only_returns_empty(self):
        """Test that whitespace-only description returns empty analysis."""
        result = await _analyze_description_async("          ")
        assert result.has_content is False

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_successful_analysis(self, mock_acompletion, mock_load_prompt):
        """Test successful description analysis."""
        mock_load_prompt.return_value = "Analyze this description: {description}"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {
                "links": [
                    {
                        "url": "https://github.com/test/repo",
                        "type": "github",
                        "label": "Source code",
                    }
                ],
                "resources": [{"name": "Tutorial PDF", "url": "https://example.com/tutorial.pdf"}],
                "relatedVideos": [{"title": "Part 2", "url": "https://youtube.com/watch?v=xyz"}],
                "socialLinks": [{"platform": "twitter", "url": "https://twitter.com/creator"}],
            }
        )
        mock_acompletion.return_value = mock_response

        description = "Check out my code at https://github.com/test/repo. Follow me on Twitter!"
        result = await _analyze_description_async(description)

        assert result.has_content is True
        assert len(result.links) == 1
        assert result.links[0].url == "https://github.com/test/repo"
        assert result.links[0].type == "github"
        assert len(result.social_links) == 1

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_llm_api_error_returns_empty(self, mock_acompletion, mock_load_prompt):
        """Test that API errors return empty analysis."""
        import litellm

        mock_load_prompt.return_value = "Analyze: {description}"
        mock_acompletion.side_effect = litellm.exceptions.APIError(
            message="API error", llm_provider="anthropic", model="test-model", status_code=500
        )

        description = "This is a test description with enough content to analyze."
        result = await _analyze_description_async(description)

        assert result.has_content is False

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_should_log_api_error_once_at_error_when_provider_already_logged_it(
        self, mock_acompletion, mock_load_prompt, caplog
    ):
        """Regression (p0.2 review): LLMProvider logs the APIError at error
        level, so the analyzer's own line is a warning — not a second error."""
        import litellm

        mock_load_prompt.return_value = "Analyze: {description}"
        mock_acompletion.side_effect = litellm.exceptions.APIError(
            message="API error", llm_provider="anthropic", model="test-model", status_code=500
        )

        with caplog.at_level(logging.WARNING):
            await _analyze_description_async(
                "This is a test description with enough content to analyze."
            )

        analyzer_levels = [
            r.levelno for r in caplog.records if r.name == "src.services.video.description_analyzer"
        ]
        assert analyzer_levels == [logging.WARNING]

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_general_exception_returns_empty(self, mock_acompletion, mock_load_prompt):
        """Test that general exceptions return empty analysis."""
        mock_load_prompt.return_value = "Analyze: {description}"
        mock_acompletion.side_effect = Exception("Unexpected error")

        description = "This is a test description with enough content to analyze."
        result = await _analyze_description_async(description)

        assert result.has_content is False

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_truncates_long_description(self, mock_acompletion, mock_load_prompt):
        """Test that long descriptions are truncated."""
        mock_load_prompt.return_value = "Analyze: {description}"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"links": []}'
        mock_acompletion.return_value = mock_response

        # Create a very long description (>5000 chars)
        long_description = "x" * 6000
        await _analyze_description_async(long_description)

        # Verify the prompt was called with truncated description
        call_args = mock_acompletion.call_args
        # The description in the messages should be truncated
        messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
        prompt_content = messages[0]["content"]
        assert "..." in prompt_content or len(prompt_content) < 6000

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_custom_fast_model(self, mock_acompletion, mock_load_prompt):
        """Test using custom fast model."""
        mock_load_prompt.return_value = "Analyze: {description}"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"links": []}'
        mock_acompletion.return_value = mock_response

        description = "This is a test description with enough content to analyze."
        custom_model = "anthropic/claude-3-haiku-20240307"
        await _analyze_description_async(description, fast_model=custom_model)

        call_args = mock_acompletion.call_args
        assert call_args.kwargs.get("model") == custom_model

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_filters_invalid_links(self, mock_acompletion, mock_load_prompt):
        """Test that links without URLs are filtered out."""
        mock_load_prompt.return_value = "Analyze: {description}"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {
                "links": [
                    {"url": "https://valid.com", "type": "article", "label": "Valid"},
                    {"url": "", "type": "article", "label": "Empty URL"},
                    {"type": "article", "label": "No URL"},
                ],
            }
        )
        mock_acompletion.return_value = mock_response

        description = "This is a test description with enough content to analyze."
        result = await _analyze_description_async(description)

        assert len(result.links) == 1
        assert result.links[0].url == "https://valid.com"


class TestDescriptionGoesThroughProvider:
    """The description call uses LLMProvider like every other stage (traced,
    timed, fakeable at ``llm_provider.acompletion``)."""

    @staticmethod
    def _response() -> MagicMock:
        response = MagicMock()
        response.choices = [MagicMock(finish_reason="stop")]
        response.choices[0].message.content = '{"links": []}'
        return response

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.record_generation")
    @patch("src.services.llm_provider.acompletion")
    async def test_should_record_a_langfuse_generation(
        self, mock_acompletion, mock_record_generation, mock_load_prompt
    ):
        mock_load_prompt.return_value = "Analyze: {description}"
        mock_acompletion.return_value = self._response()

        await _analyze_description_async("A description long enough to analyze, with links.")

        assert mock_record_generation.call_args.kwargs["span_name"] == "description_analysis"

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_should_land_in_the_run_timing(self, mock_acompletion, mock_load_prompt):
        from src.services.pipeline import pipeline_timing

        mock_load_prompt.return_value = "Analyze: {description}"
        mock_acompletion.return_value = self._response()
        recorder = pipeline_timing.start_run_timing()

        await _analyze_description_async("A description long enough to analyze, with links.")

        assert [c["span"] for c in recorder.llm_calls] == ["description_analysis"]


class TestAnalyzeDescription:
    """Tests for the main analyze_description function."""

    @patch("src.services.video.description_analyzer._analyze_description_async")
    async def test_calls_async_function(self, mock_async_analyze):
        """Test that analyze_description calls the async implementation."""
        mock_async_analyze.return_value = DescriptionAnalysis()

        description = "Test description"
        await analyze_description(description)

        mock_async_analyze.assert_called_once_with(description, None)

    @patch("src.services.video.description_analyzer._analyze_description_async")
    async def test_passes_fast_model(self, mock_async_analyze):
        """Test that fast_model is passed through."""
        mock_async_analyze.return_value = DescriptionAnalysis()

        description = "Test description"
        fast_model = "custom/model"
        await analyze_description(description, fast_model=fast_model)

        mock_async_analyze.assert_called_once_with(description, fast_model)


class TestLoadPrompt:
    """Tests for load_prompt function."""

    @patch("pathlib.Path.read_text")
    def test_load_prompt_success(self, mock_read_text):
        """Test successful prompt loading."""
        mock_read_text.return_value = "Test prompt template: {description}"

        result = load_prompt("description_analysis")

        assert result == "Test prompt template: {description}"

    @patch("pathlib.Path.read_text")
    def test_load_prompt_file_not_found(self, mock_read_text):
        """Test error when prompt file not found."""
        mock_read_text.side_effect = FileNotFoundError("File not found")

        with pytest.raises(FileNotFoundError):
            load_prompt("nonexistent_prompt")


class TestDescriptionRetry:
    """1d.5: the analysis gets one retry and resends a dropped connection at once."""

    _DESCRIPTION = "Chapters: 0:00 intro, 2:10 the sauce, 9:45 baking the lasagna in the oven."

    @staticmethod
    def _reply() -> MagicMock:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = json.dumps(
            {"timestamps": [{"time": "2:10", "label": "the sauce"}]}
        )
        return response

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_should_recover_on_the_retry_after_a_transient_error(
        self, mock_acompletion, mock_load_prompt
    ):
        import litellm

        mock_load_prompt.return_value = "Analyze: {description}"
        mock_acompletion.side_effect = [
            litellm.exceptions.InternalServerError(
                message="overloaded", llm_provider="openai", model="gpt-4o-mini"
            ),
            self._reply(),
        ]

        result = await _analyze_description_async(self._DESCRIPTION)

        assert [t.label for t in result.timestamps] == ["the sauce"]

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_should_give_up_after_one_retry(self, mock_acompletion, mock_load_prompt):
        import litellm

        mock_load_prompt.return_value = "Analyze: {description}"
        mock_acompletion.side_effect = litellm.exceptions.InternalServerError(
            message="overloaded", llm_provider="openai", model="gpt-4o-mini"
        )

        await _analyze_description_async(self._DESCRIPTION)

        assert mock_acompletion.await_count == 2

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_should_resend_a_dropped_connection_without_spending_the_retry(
        self, mock_acompletion, mock_load_prompt
    ):
        import litellm

        mock_load_prompt.return_value = "Analyze: {description}"
        overloaded = litellm.exceptions.InternalServerError(
            message="overloaded", llm_provider="openai", model="gpt-4o-mini"
        )
        dropped = litellm.exceptions.Timeout(
            message="Connection timed out", model="gpt-4o-mini", llm_provider="openai"
        )
        mock_acompletion.side_effect = [dropped, overloaded, self._reply()]

        result = await _analyze_description_async(self._DESCRIPTION)

        assert result.timestamps and mock_acompletion.await_count == 3


class TestDescriptionTotalCap:
    """G21-4: both attempts, the backoff and any pause fit in one total cap."""

    _DESCRIPTION = "Chapters: 0:00 intro, 2:10 the sauce, 9:45 baking the lasagna in the oven."

    @staticmethod
    async def _hang(**_kwargs: object) -> MagicMock:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    @pytest.fixture
    def short_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.services.video import description_analyzer

        monkeypatch.setattr(description_analyzer, "DESCRIPTION_TOTAL_SECONDS", 0.05)

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_should_return_an_empty_analysis_when_the_total_cap_hits(
        self, mock_acompletion, mock_load_prompt, short_cap
    ):
        mock_load_prompt.return_value = "Analyze: {description}"
        mock_acompletion.side_effect = self._hang

        result = await asyncio.wait_for(_analyze_description_async(self._DESCRIPTION), timeout=2)

        assert result.has_content is False

    @patch("src.services.video.description_analyzer.load_prompt")
    @patch("src.services.llm_provider.acompletion")
    async def test_should_record_the_capped_attempt_as_a_failure(
        self, mock_acompletion, mock_load_prompt, short_cap
    ):
        from src.services.pipeline import pipeline_timing

        mock_load_prompt.return_value = "Analyze: {description}"
        mock_acompletion.side_effect = self._hang
        recorder = pipeline_timing.start_run_timing()

        await _analyze_description_async(self._DESCRIPTION)

        assert [f["span"] for f in recorder.llm_failures] == ["description_analysis"]

    def test_should_keep_one_attempt_shorter_than_the_total_cap(self):
        from src.services.video import description_analyzer as da

        assert da.DESCRIPTION_ATTEMPT_SECONDS < da.DESCRIPTION_TOTAL_SECONDS <= 30.0
