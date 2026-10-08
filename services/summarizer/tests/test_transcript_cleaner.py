"""Tests for advanced transcript cleaning service."""

from src.services.transcript.cleaner import (
    clean_transcript_advanced,
    collapse_repetitions,
    remove_fillers,
)


class TestRemoveFillers:
    """Test filler word removal."""

    def test_removes_common_fillers(self):
        text = "So um I think uh this is basically a good idea so yeah"
        result = remove_fillers(text)
        assert "um" not in result.lower().split()
        assert "uh" not in result.lower().split()
        assert "basically" not in result.lower()
        assert "good idea" in result

    def test_preserves_meaningful_words(self):
        text = "The algorithm runs in O(n) time and uses a hash sort of mechanism"
        result = remove_fillers(text)
        assert "algorithm" in result
        assert "O(n)" in result
        assert "hash" in result

    def test_preserves_technical_terms(self):
        text = "Use React useState hook for state management"
        result = remove_fillers(text)
        assert "React" in result
        assert "useState" in result
        assert "state management" in result

    def test_preserves_numbers(self):
        text = "The function returns 42 and um takes 3 parameters"
        result = remove_fillers(text)
        assert "42" in result
        assert "3" in result

    def test_empty_text(self):
        assert remove_fillers("") == ""
        assert remove_fillers(None) is None

    def test_no_fillers_unchanged(self):
        text = "The quick brown fox jumps over the lazy dog."
        result = remove_fillers(text)
        assert result == text

    def test_case_insensitive(self):
        text = "UM UH basically BASICALLY So Yeah WELL BASICALLY"
        result = remove_fillers(text)
        # All fillers should be removed regardless of case
        assert result.strip() == ""

    def test_should_drop_commas_stranded_between_clauses(self):
        assert remove_fillers("So, um, uh, I think we start.") == "So, I think we start."

    def test_should_drop_a_comma_stranded_before_sentence_end(self):
        assert remove_fillers("And that's it, um. Next step") == "And that's it. Next step"

    def test_should_drop_a_comma_stranded_at_the_start(self):
        assert remove_fillers("Um, so we go") == "so we go"

    def test_should_keep_an_abbreviation_comma_when_a_filler_is_removed_elsewhere(self):
        text = "Use e.g., flour, um, and sugar."

        assert remove_fillers(text) == "Use e.g., flour, and sugar."

    def test_should_keep_the_comma_after_an_initialism_when_a_filler_is_removed(self):
        assert remove_fillers("In the U.S., um, people bake") == "In the U.S., people bake"

    def test_should_drop_the_comma_a_mid_sentence_filler_carried(self):
        assert remove_fillers("and um, then we bake") == "and then we bake"

    def test_should_leave_punctuation_alone_when_nothing_was_removed(self):
        text = "Use e.g., flour , or  sugar."
        assert remove_fillers(text) == text


class TestCollapseRepetitions:
    """Test near-duplicate sentence removal."""

    def test_removes_exact_duplicates(self):
        sentences = [
            "The weather is nice today.",
            "The weather is nice today.",
            "Something completely different.",
        ]
        result = collapse_repetitions(sentences)
        assert len(result) == 2

    def test_removes_near_duplicates(self):
        sentences = [
            "The weather is really nice today and I love going to the park.",
            "The weather is really nice today and I love going to the park too.",
            "Python is a programming language used for data science.",
        ]
        result = collapse_repetitions(sentences, threshold=0.85)
        # Near-duplicates should be collapsed
        assert len(result) <= 2

    def test_preserves_different_sentences(self):
        sentences = [
            "Python is a programming language.",
            "JavaScript runs in the browser.",
            "Rust is memory safe.",
        ]
        result = collapse_repetitions(sentences)
        assert len(result) == 3

    def test_single_sentence_unchanged(self):
        sentences = ["Hello world."]
        result = collapse_repetitions(sentences)
        assert result == sentences

    def test_empty_list(self):
        assert collapse_repetitions([]) == []

    def test_preserves_order(self):
        sentences = ["First.", "Second.", "Third."]
        result = collapse_repetitions(sentences)
        assert result == sentences

    def test_skips_tfidf_above_300_sentences(self):
        """TF-IDF is capped at 300 sentences to avoid O(n^2) blocking."""
        sentences = [f"Unique sentence number {i} about topic {i}." for i in range(400)]
        result = collapse_repetitions(sentences)
        # Should return all sentences unchanged (skips TF-IDF)
        assert len(result) == 400


class TestCleanTranscriptAdvanced:
    """Test the advanced pass (sentence segmentation + repetition collapse)."""

    def test_should_collapse_repeated_sentences(self):
        text = (
            "Machine learning is a way to teach computers. "
            "Machine learning is a way to teach computers. "
            "Computers can learn patterns from data."
        )
        result = clean_transcript_advanced(text)
        assert result.count("Machine learning is a way to teach computers.") == 1

    def test_should_leave_filler_removal_to_basic_cleaning(self):
        text = (
            "So basically we talk about machine learning today. "
            "Computers can learn patterns from data."
        )
        result = clean_transcript_advanced(text)
        assert "basically" in result

    def test_short_text_passthrough(self):
        text = "Hi there."
        result = clean_transcript_advanced(text)
        assert result == text

    def test_empty_text(self):
        assert clean_transcript_advanced("") == ""
        assert clean_transcript_advanced(None) == ""

    def test_preserves_code_content(self):
        text = (
            "First we import numpy. Then we create an array with numpy dot zeros. "
            "The function takes a shape parameter like 3 comma 4. "
            "This returns a 3 by 4 matrix of zeros."
        )
        result = clean_transcript_advanced(text)
        assert "numpy" in result
        assert "array" in result
        assert "3" in result
