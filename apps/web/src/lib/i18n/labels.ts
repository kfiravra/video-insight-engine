/** Noun forms by CLDR plural category. `{count}` is replaced by the formatted
 *  number; `other` is required and covers any category a language leaves out
 *  (English has one / other, Hebrew adds two, Arabic all six). */
export interface PluralForms {
  other: string;
  zero?: string;
  one?: string;
  two?: string;
  few?: string;
  many?: string;
}

/** Unit templates for a countdown ("7h 30m"); `{count}` is the number. */
export interface DurationLabels {
  days: string;
  hours: string;
  minutes: string;
}

/** The minutes UI: usage tile, "not enough minutes" callout, /settings/billing and
 *  the playlist import result. Placeholders in braces are interpolated by the
 *  caller; `{when}` is a countdown from `formatDurationUntil`, `{date}` a
 *  formatted date, and `{minutes}` / `{needed}` / `{available}` / `{max}` /
 *  `{total}` / `{running}` a noun phrase from the plural forms. */
export interface BillingLabels {
  minutes: PluralForms;
  videos: PluralForms;
  duration: DurationLabels;
  // Usage tile
  tileTitle: string;
  tileAria: string;
  tileLoading: string;
  tileError: string;
  planFree: string;
  planPro: string;
  freeVideo: string;
  freeVideoReady: string;
  freeVideoNext: string;
  freeVideoUnavailable: string;
  freeVideoUpTo: string;
  planMinutes: string;
  /** {left} is a bare number, {total} a minutes phrase: "412 of 600 minutes left". */
  planMinutesLeft: string;
  planMinutesResets: string;
  credits: string;
  inProgress: string;
  blocked: string;
  manageLink: string;
  // Not-enough-minutes callout (402 / 422)
  calloutFreeUsed: string;
  calloutFreeNext: string;
  calloutNeeds: string;
  calloutNeedsUnknown: string;
  calloutTooLong: string;
  calloutTooLongUnknown: string;
  calloutCta: string;
  // Other refusals at submit time
  errorTooManyRuns: string;
  errorTooManyRunsUnknown: string;
  errorRateLimited: string;
  errorChatLimit: string;
  errorChatLimitNext: string;
  // /settings/billing
  pageTitle: string;
  pageIntro: string;
  back: string;
  planSection: string;
  planTermEnds: string;
  planFreeNote: string;
  cycleEnds: string;
  minutesSection: string;
  lotsSection: string;
  lotsEmpty: string;
  lotExpires: string;
  lotNoExpiry: string;
  holdsSection: string;
  holdsEmpty: string;
  holdVideoFallback: string;
  teamNote: string;
  // Playlist import result
  playlistResult: string;
  playlistFailedHeading: string;
  playlistDismiss: string;
  failedInsufficient: string;
  failedTooLong: string;
  failedBlocked: string;
  failedTooManyRuns: string;
  failedRateLimited: string;
  failedUnknown: string;
}

/** The UI labels every supported language fills (en / he / ar) — the
 *  interface makes a missing key a compile error. */
export interface Labels {
  // Navigation
  next: string;
  previous: string;
  prev: string;
  // Hero chrome
  processing: string;
  hide: string;
  watch: string;
  /** Accessible name of a seek chip (▶ m:ss) — {time} is interpolated, so
   *  screen readers hear the page's language around the timestamp. */
  jumpTo: string;
  // Language toggle (announced to screen readers; the visible pill labels
  // are the language names themselves and stay in their native script).
  contentLanguage: string;
  // Actions
  done: string;
  undo: string;
  copy: string;
  copied: string;
  // Quiz / Assessment
  correct: string;
  tryAgain: string;
  review: string;
  missed: string;
  // Flashcards
  gotIt: string;
  reviewAgain: string;
  cardsReviewed: string;
  // Code explorer
  showAll: string;
  stepThrough: string;
  // Spot explorer
  all: string;
  tip: string;
  // Exercise
  formCue: string;
  hideFormCue: string;
  duration: string;
  equipment: string;
  completeSet: string;
  workoutComplete: string;
  allSetsFinished: string;
  exerciseComplete: string;
  skipRest: string;
  soundOn: string;
  soundOff: string;
  muteAudioCues: string;
  enableAudioCues: string;
  // Comparison
  description: string;
  example: string;
  goForIt: string;
  /** Eyebrow over one competitor's table — {name} is interpolated. */
  versusName: string;
  /** Who answers an interview question — {speaker} is interpolated ("A · Tegmark"). */
  answerBy: string;
  /** Column head for the other side of a review's comparison. */
  competitor: string;
  // Overview
  level: string;
  items: string;
  brief: string;
  keyTakeaways: string;
  continueExploring: string;
  highlights: string;
  moreTakeaways: string;
  /** Accessible names for the hero's collapse controls — three buttons would
   *  otherwise all be called "Hide". */
  hideBrief: string;
  hideTakeaways: string;
  /** The hero's "should I watch?" lines (output-quality 2.3). */
  worthItFor: string;
  skipIf: string;
  /** Phones: the collapsed hero row that holds those two lines (4.4). */
  worthIt: string;
  /** A review's hero verdict row and the reviewer's lists that replace those lines (4.3). */
  verdict: string;
  bestFor: string;
  notFor: string;
  /** The hero's reading-time promise and the video's length beside it;
   *  {minutes} is interpolated. */
  readMinutes: string;
  watchMinutes: string;
  /** Strip overflow pill; {count} is interpolated. */
  showMoreSections: string;
  /** Strip overflow pill when exactly one tab is hidden. */
  showOneMoreSection: string;
  tips: string;
  // Verdict
  price: string;
  score: string;
  /** Accessible name of the hero verdict's stated sub-scores ("Battery 9/10" chips). */
  subScores: string;
  /** Desktop hero (4.6a): the visible "more" that unrolls a two-line brief,
   *  and its accessible name (it contains the visible word). */
  more: string;
  moreBrief: string;
  /** Desktop hero (output-quality pC): a cut worth-it line's toggle — the
   *  accessible names hold the visible "more" / "Hide"; {label} is the line's label. */
  moreLine: string;
  hideLine: string;
  /** A review's verdict badge, by the stored value (recommended / conditional /
   *  not_recommended / best_in_class); an unknown value prints as stored. */
  verdictBadges: Record<string, string>;
  // Comparison radar
  /** Under a radar with lower-is-better spokes; {features} is interpolated. */
  radarLowerWins: string;
  // Canvases (list ⇄ graph switch) and the concept list's labelled lines
  viewList: string;
  viewMap: string;
  viewFlow: string;
  analogy: string;
  // Enter-mode runner (Study / Listen)
  revealAnswer: string;
  watchThisPart: string;
  /** Listen mode's play control; {time} / {start} / {end} are interpolated. */
  playFrom: string;
  playRange: string;
  // Library chat (cross-video assistant when no single video is open)
  libraryChatPlaceholder: string;
  libraryChatEmptyTitle: string;
  libraryChatEmptyBody: string;
  // Assistant actions (confirm-before-execute flow in the sidebar chat).
  // {action} is interpolated with one of the actionNames values.
  actionConfirmPrompt: string;
  actionConfirm: string;
  actionCancel: string;
  actionCancelled: string;
  actionRunning: string;
  actionSuccess: string;
  actionFailed: string;
  actionNames: {
    organize_library: string;
    generate_video: string;
    create_folder: string;
  };
  // Tab preview tooltips (keyed by TabEntry.id). Keys the app currently
  // ships are listed in EN below; callers fall back gracefully when a key
  // is absent, so translators can stage coverage incrementally.
  tabPreviews: Record<string, string>;
  // Minutes, plan and billing page
  billing: BillingLabels;
}
