import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import {
  StreamingPlaceholder,
  StreamErrorCard,
} from "@/features/video-output/components/StreamingPlaceholder";
import type { TriageResult } from "@vie/types";
import { DirectionProvider } from "@/contexts/DirectionContext";

const TRIAGE: TriageResult = {
  contentTags: ["tech"],
  modifiers: [],
  primaryTag: "tech",
  userGoal: "Learn the tool",
  tabs: [],
  confidence: 0.9,
};

describe("StreamingPlaceholder", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("should render every base stage of the timeline", () => {
    render(<StreamingPlaceholder streamPhase="metadata" />);

    expect(screen.getByText("Read the video")).toBeInTheDocument();
    expect(screen.getByText("Get the transcript")).toBeInTheDocument();
    expect(screen.getByText("Extract the knowledge")).toBeInTheDocument();
    expect(screen.getByText("Build your tabs")).toBeInTheDocument();
    expect(screen.queryByText("Translate")).not.toBeInTheDocument();
  });

  it("should append the Translate stage only when the translation phase begins", () => {
    render(<StreamingPlaceholder streamPhase="translation" />);

    expect(screen.getByText("Translate")).toBeInTheDocument();
  });

  it("should explain the audio-transcription wait on the active transcript stage", () => {
    render(<StreamingPlaceholder streamPhase="transcript" phaseDetail="audio-transcription" />);

    expect(screen.getByText(/audio is being transcribed/i)).toBeInTheDocument();
  });

  it("should say YouTube is rate-limiting while the metadata fetch retries", () => {
    render(<StreamingPlaceholder streamPhase="metadata" phaseDetail="youtube-retrying" />);

    expect(screen.getByText("YouTube is rate-limiting, retrying")).toBeInTheDocument();
  });

  it("should say YouTube is rate-limiting while the caption fetch retries", () => {
    render(<StreamingPlaceholder streamPhase="transcript" phaseDetail="youtube-retrying" />);

    expect(screen.getByText("YouTube is rate-limiting, retrying")).toBeInTheDocument();
  });

  it("should show the detected domain and batch progress during extraction", () => {
    render(
      <StreamingPlaceholder
        streamPhase="extraction"
        triage={TRIAGE}
        extractionProgress={{ section: "chunked", percent: 40, batch: 2, of: 5 }}
      />,
    );

    expect(screen.getByText(/detected:/i)).toBeInTheDocument();
    expect(screen.getByText(/tech/i)).toBeInTheDocument();
    expect(screen.getByText("2 of 5")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /extraction progress/i })).toHaveAttribute(
      "aria-valuenow",
      "40",
    );
  });

  it("should reveal Cancel only after the delay, with space reserved from mount", () => {
    const onCancel = vi.fn();
    render(<StreamingPlaceholder streamPhase="metadata" onCancel={onCancel} />);

    const button = screen.getByText("Cancel");
    expect(button).toHaveAttribute("aria-hidden", "true");
    expect(button).toHaveAttribute("tabindex", "-1");

    act(() => {
      vi.advanceTimersByTime(5100);
    });

    expect(button).not.toHaveAttribute("aria-hidden", "true");
    expect(button).toHaveAttribute("tabindex", "0");
  });

  it("should rotate the feature spotlight over time", () => {
    render(<StreamingPlaceholder streamPhase="metadata" />);

    expect(screen.getByText(/instant next time/i)).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(6600);
    });

    expect(screen.queryByText(/instant next time/i)).not.toBeInTheDocument();
    expect(screen.getByText(/every claim has a timestamp/i)).toBeInTheDocument();
  });

  it("should show analyzed frames with a running count", () => {
    render(
      <StreamingPlaceholder
        streamPhase="extraction"
        frames={[
          { index: 0, timestamp: 10, url: "https://cdn.test/f0.jpg" },
          { index: 1, timestamp: 20, url: "https://cdn.test/f1.jpg" },
        ]}
      />,
    );

    expect(screen.getByText("2 frames analyzed")).toBeInTheDocument();
  });

  it("should surface the latest warning", () => {
    render(
      <StreamingPlaceholder
        streamPhase="building"
        warnings={["First warning", "Enrichment partially failed"]}
      />,
    );

    expect(screen.getByText("Enrichment partially failed")).toBeInTheDocument();
    expect(screen.queryByText("First warning")).not.toBeInTheDocument();
  });
});

describe("StreamErrorCard", () => {
  it("should render the message and call onRetry", () => {
    const onRetry = vi.fn();
    render(<StreamErrorCard message="The AI service timed out." onRetry={onRetry} />);

    expect(screen.getByText("The AI service timed out.")).toBeInTheDocument();
    screen.getByRole("button", { name: /retry/i }).click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("should fall back to a generic message when none is provided", () => {
    render(<StreamErrorCard onRetry={() => {}} />);

    expect(screen.getByText(/something went wrong while processing/i)).toBeInTheDocument();
  });

  describe("for a video whose run already failed (VIDEO_FAILED)", () => {
    it("should say Retry starts a new run, not show the stored message or the code", () => {
      render(<StreamErrorCard message="KeyError: 'tabs'" code="VIDEO_FAILED" onRetry={() => {}} />);

      expect(
        screen.getByText("This video didn't finish processing. Retry starts it again as a new run."),
      ).toBeInTheDocument();
      expect(screen.queryByText(/KeyError|VIDEO_FAILED/)).toBeNull();
    });

    it("should still offer Retry", () => {
      const onRetry = vi.fn();
      render(<StreamErrorCard code="VIDEO_FAILED" onRetry={onRetry} />);

      screen.getByRole("button", { name: /retry/i }).click();

      expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it.each([
      ["he", "הסרטון לא סיים להיות מעובד. ניסיון חוזר יפעיל אותו מחדש כהרצה חדשה."],
      ["ar", "لم تكتمل معالجة هذا الفيديو. ستبدأ إعادة المحاولة تشغيلًا جديدًا."],
    ])("should say it in %s under that direction context", (language, expected) => {
      render(
        <DirectionProvider language={language} isRTL>
          <StreamErrorCard message="raw" code="VIDEO_FAILED" onRetry={() => {}} />
        </DirectionProvider>,
      );

      expect(screen.getByText(expected)).toBeInTheDocument();
    });

    it("should leave the message of any other error code alone", () => {
      render(<StreamErrorCard message="The AI service timed out." code="LLM_ERROR" onRetry={() => {}} />);

      expect(screen.getByText("The AI service timed out.")).toBeInTheDocument();
    });
  });
});
