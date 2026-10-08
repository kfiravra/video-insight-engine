"""Pre-LLM transcript cleaning: filler removal + advanced repetition collapse.

``remove_fillers`` is part of BASIC cleaning — ``clean_transcript`` calls it, so
``clean_text`` and every rendered prompt transcript are filler-free whatever
``TRANSCRIPT_CLEANING_ENABLED`` says.

``clean_transcript_advanced`` is the optional pass behind that flag: spaCy
sentence segmentation + TF-IDF near-duplicate collapse on basic-cleaned text.

The filler list holds only phrases that are never content. Basic cleaning runs
on every English transcript, so a phrase that is a filler only most of the time
would corrupt the rest: "you know", "i mean", "kind of", "sort of", "okay so"
and "alright so" are excluded because "do you know how", "what I mean is",
"what kind of flour", "sort of the dough" and "it's okay so we" need them.
"""

import logging
import re
import threading

logger = logging.getLogger(__name__)

# Phrases that are never content (see the module docstring for what is excluded
# and why). "like", "right", "actually" carry meaning in many contexts too.
FILLERS = {
    "um",
    "uh",
    "uh huh",
    "basically",
    "so yeah",
    "well basically",
}

# Build regex pattern from filler set (longest first to avoid partial matches)
_sorted_fillers = sorted(FILLERS, key=len, reverse=True)
_FILLER_ALTERNATION = "|".join(re.escape(f) for f in _sorted_fillers)

# A filler with the commas that punctuated (manual) captions put around it.
# Only these commas are touched — "e.g., x" or "U.S.," elsewhere in the text
# keep theirs.
_FILLER_WITH_COMMAS = re.compile(
    r"(?P<lead>,\s*)?\b(?:" + _FILLER_ALTERNATION + r")\b(?P<trail>\s*,)?",
    re.IGNORECASE,
)
_CLAUSE_END = re.compile(r"\s*(?:[.!?]|$)")

# Lazy-loaded spaCy model (thread-safe)
_nlp = None
_nlp_lock = threading.Lock()


def _get_nlp():
    """Lazy-load spaCy model for sentence segmentation (thread-safe)."""
    global _nlp
    if _nlp is None:
        with _nlp_lock:
            if _nlp is None:
                import spacy

                _nlp = spacy.load("en_core_web_sm")
                logger.info("Loaded spaCy model: en_core_web_sm")
    return _nlp


def remove_fillers(text: str) -> str:
    """Remove filler words/phrases from text.

    Conservative approach: only removes phrases from the curated FILLERS set.
    Preserves capitalization and meaningful words; a comma the removal strands
    is dropped, other punctuation is kept. Text without fillers is unchanged.
    """
    if not text:
        return text

    cleaned, removed = _FILLER_WITH_COMMAS.subn(_drop_filler, text)
    if not removed:
        return text
    # Collapse multiple spaces left by removal
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _drop_filler(match: re.Match[str]) -> str:
    """What replaces one filler: its own trailing comma goes with it.

    "So, um, I" keeps the comma before the filler ("So, I"); "that's it, um."
    loses it too, since a clause end follows ("that's it."); "Um, so" → "so".
    """
    lead = match.group("lead") or ""
    if lead and not match.group("trail") and _CLAUSE_END.match(match.string, match.end()):
        return ""
    return lead


def collapse_repetitions(
    sentences: list[str],
    threshold: float = 0.9,
) -> list[str]:
    """Remove near-duplicate sentences using TF-IDF cosine similarity.

    Args:
        sentences: List of sentence strings.
        threshold: Similarity threshold (0.0-1.0). Higher = more aggressive.
            Default 0.9 is conservative — only near-exact duplicates removed.

    Returns:
        Deduplicated list preserving original order.
    """
    if len(sentences) < 2:
        return sentences

    # Cap at 300 sentences to avoid O(n²) TF-IDF comparison blocking the event loop.
    # Long transcripts (>300 sentences) rarely have repetitions worth the CPU cost.
    if len(sentences) > 300:
        logger.info("Skipping TF-IDF collapse: %d sentences exceeds 300 cap", len(sentences))
        return sentences

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        logger.warning("scikit-learn not available, skipping repetition collapse")
        return sentences

    try:
        vectorizer = TfidfVectorizer()
        tfidf = vectorizer.fit_transform(sentences)
    except ValueError:
        # Empty vocabulary (e.g., all stop words)
        return sentences

    keep = [True] * len(sentences)
    for i in range(1, len(sentences)):
        if not keep[i]:
            continue
        for j in range(i):
            if not keep[j]:
                continue
            sim = cosine_similarity(tfidf[i : i + 1], tfidf[j : j + 1])[0][0]
            if sim > threshold:
                keep[i] = False
                break

    removed = sum(1 for k in keep if not k)
    if removed:
        logger.debug("Collapsed %d repetitive sentences", removed)

    return [s for s, k in zip(sentences, keep) if k]


def clean_transcript_advanced(text: str) -> str:
    """Advanced cleaning pass (behind ``TRANSCRIPT_CLEANING_ENABLED``).

    Steps:
    1. Segment into sentences (spaCy)
    2. Collapse near-duplicate sentences (TF-IDF)
    3. Rejoin and normalize whitespace

    Call on basic-cleaned text (``clean_transcript``): fillers are already gone.
    """
    if not text or len(text.strip()) < 50:
        return text.strip() if text else ""

    original_len = len(text)

    # Step 1: Sentence segmentation
    # Cap at 100K chars to avoid excessive memory usage in spaCy's full pipeline.
    # Typical 2-hour video transcript is ~30K chars; 100K handles up to ~6 hours.
    if len(text) > 100_000:
        logger.info(
            "Transcript too long for spaCy (%d chars), using regex sentence split", len(text)
        )
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    else:
        nlp = _get_nlp()
        doc = nlp(text)
        sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]

    if not sentences:
        return text

    # Step 2: Collapse repetitions
    sentences = collapse_repetitions(sentences)

    # Step 3: Rejoin and normalize
    result = " ".join(sentences)
    result = re.sub(r"\s{2,}", " ", result).strip()

    cleaned_len = len(result)
    reduction = (1 - cleaned_len / original_len) * 100 if original_len else 0
    logger.info(
        "Transcript cleaning: %d → %d chars (%.1f%% reduction)",
        original_len,
        cleaned_len,
        reduction,
    )

    return result
