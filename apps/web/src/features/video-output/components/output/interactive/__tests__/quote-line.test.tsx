/// <reference types="@testing-library/jest-dom" />
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { QuoteLine } from '../QuoteLine';
import { SpotExplorer } from '../SpotExplorer';
import { MomentTrack } from '../MomentTrack';
import { TAB_PROP_SCHEMAS } from '@/features/video-output/lib/tab-prop-schemas';

const HEBREW = 'אין לי זמן לזה';
const ENGLISH = 'I have no time for this';

describe('QuoteLine', () => {
  it('should render the English beneath the verbatim quote when quoteEn is set', () => {
    render(<QuoteLine quote={HEBREW} quoteEn={ENGLISH} />);
    expect(screen.getByText(ENGLISH)).toBeInTheDocument();
  });

  it('should keep the verbatim quote as a quotation when quoteEn is set', () => {
    render(<QuoteLine quote={HEBREW} quoteEn={ENGLISH} />);
    expect(screen.getByText(HEBREW).closest('blockquote')).not.toBeNull();
  });

  it('should read a Hebrew quote right to left inside an English page when direction is auto', () => {
    render(<QuoteLine quote={HEBREW} quoteEn={ENGLISH} />);
    expect(screen.getByText(HEBREW).closest('blockquote')).toHaveAttribute('dir', 'auto');
  });

  it('should render only the quote when quoteEn is absent', () => {
    const { container } = render(<QuoteLine quote={HEBREW} />);
    expect(container.querySelectorAll('p')).toHaveLength(0);
  });

  it('should render nothing when there is no quote', () => {
    const { container } = render(<QuoteLine quote="  " quoteEn={ENGLISH} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('should render the English as prose when the quote is flagged translated', () => {
    render(<QuoteLine quote={HEBREW} quoteEn={ENGLISH} translated />);
    expect(screen.getByText(ENGLISH).closest('blockquote')).toBeNull();
  });

  it('should not repeat the English when it equals the quote', () => {
    render(<QuoteLine quote={ENGLISH} quoteEn={ENGLISH} />);
    expect(screen.getAllByText(ENGLISH)).toHaveLength(1);
  });
});

describe('quote surfaces', () => {
  it('should show the English beneath a spot card quote when the spot carries quoteEn', () => {
    render(<SpotExplorer spots={[{ name: 'Idea', emoji: '💡', description: 'd', quote: HEBREW, quoteEn: ENGLISH }]} />);
    expect(screen.getByText(ENGLISH)).toBeInTheDocument();
  });

  it('should show the English beneath a moment quote when the moment carries quoteEn', () => {
    render(<MomentTrack items={[{ time: '0:10', seconds: 10, label: 'Line', description: 'Context.', quote: HEBREW, quoteEn: ENGLISH }]} />);
    expect(screen.getByText(ENGLISH)).toBeInTheDocument();
  });

  it('should accept quoteEn on spot, moment and claim items when validating tab props', () => {
    const spot = { spots: [{ name: 'a', quote: HEBREW, quoteEn: ENGLISH }] };
    const moment = { items: [{ label: 'a', seconds: 1, quote: HEBREW, quoteEn: ENGLISH }] };
    const claim = { claims: [{ claim: 'a', quote: HEBREW, quoteEn: ENGLISH }] };
    const ok = [
      TAB_PROP_SCHEMAS.spot_explorer.schema.safeParse(spot).success,
      TAB_PROP_SCHEMAS.moment_track.schema.safeParse(moment).success,
      TAB_PROP_SCHEMAS.claims_tracker.schema.safeParse(claim).success,
    ];
    expect(ok).toEqual([true, true, true]);
  });

  it('should refuse a non-string quoteEn when validating spot props', () => {
    const bad = { spots: [{ name: 'a', quoteEn: 5 }] };
    expect(TAB_PROP_SCHEMAS.spot_explorer.schema.safeParse(bad).success).toBe(false);
  });
});
