FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (ffmpeg for Whisper and frames, tesseract for OCR).
# nodejs is not used by yt-dlp: its JS challenges run on Deno, copied in below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Deno is yt-dlp's default-enabled JavaScript runtime for YouTube's JS challenges
# (yt-dlp-ejs). Requires >= 2.3.0; pinned to the version yt-dlp's pin-deno extra uses.
COPY --from=denoland/deno:bin-2.9.5 /deno /usr/local/bin/deno

# Install Python dependencies from the uv-compiled lockfile (reproducible
# builds — regenerate with the command in the lockfile header). The lock pins
# torch==<ver>+cpu, whose wheel only exists on the PyTorch CPU index; the
# default PyPI wheel would bundle ~1.5GB of CUDA libraries this CPU-only
# service never uses. --timeout/--retries harden the large downloads against
# transient read timeouts.
COPY services/summarizer/requirements.lock .
RUN pip install --no-cache-dir --timeout 120 --retries 5 \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.lock

# Install shared llm-common package. --no-deps: its dependency tree is already
# pinned by requirements.lock (compiled from requirements.txt + the package's
# own pyproject.toml), so resolving again would bypass the lock.
COPY packages/llm-common/ /tmp/llm-common/
RUN pip install --no-cache-dir --no-deps /tmp/llm-common/ && rm -rf /tmp/llm-common/

# Download spaCy model for sentence segmentation
RUN python -m spacy download en_core_web_sm

# Non-root runtime user. Needs: writable frame storage, a writable HOME for
# runtime model caches (HuggingFace/sentence-transformers download on first
# use), and /tmp for yt-dlp scratch (world-writable already).
RUN useradd --create-home --uid 10001 app \
    && mkdir -p storage/frames \
    && chown -R app:app /app

# Copy source
COPY --chown=app:app services/summarizer/src/ ./src/

USER app
ENV HOME=/home/app

# Health endpoint
EXPOSE 8000

# Run FastAPI server
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
