import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { TeamContactLink } from '../TeamContactLink';
import { PaymentsPlaceholderNotice } from '../PaymentsPlaceholderNotice';
import { renderIn } from '@/components/usage/__tests__/usage-fixtures';
import { supportEmail, supportMailto } from '@/lib/support';

describe('supportEmail', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    ['empty', '', null],
    ['blank', '   ', null],
    ['not an address', 'support', null],
    ['two addresses', 'a@vie.ad b@vie.ad', null],
    ['an address', 'support@vie.ad', 'support@vie.ad'],
    ['an address with spaces around it', '  support@vie.ad ', 'support@vie.ad'],
  ])('should read %s as %s', (_name, value, expected) => {
    vi.stubEnv('VITE_SUPPORT_EMAIL', value);

    expect(supportEmail()).toBe(expected);
  });

  it('should encode the subject of the mailto link', () => {
    expect(supportMailto('support@vie.ad', 'Tokens for my account')).toBe(
      'mailto:support@vie.ad?subject=Tokens%20for%20my%20account',
    );
  });
});

describe('TeamContactLink', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('should render nothing without a support address', () => {
    vi.stubEnv('VITE_SUPPORT_EMAIL', '');

    renderIn(<TeamContactLink />);

    expect(screen.queryByTestId('team-contact')).toBeNull();
  });

  it('should link to the support address with a subject, showing the address', () => {
    vi.stubEnv('VITE_SUPPORT_EMAIL', 'support@vie.ad');

    renderIn(<TeamContactLink />);

    const link = screen.getByRole('link', { name: 'Email the team: support@vie.ad' });
    expect(link).toHaveAttribute('href', 'mailto:support@vie.ad?subject=Tokens%20for%20my%20account');
  });

  it('should word the link and its subject in the page language', () => {
    vi.stubEnv('VITE_SUPPORT_EMAIL', 'support@vie.ad');

    renderIn(<TeamContactLink />, 'he');

    const link = screen.getByTestId('team-contact');
    expect(link).toHaveTextContent('כתבו לצוות: support@vie.ad');
    expect(link.getAttribute('href')).toBe(`mailto:support@vie.ad?subject=${encodeURIComponent('טוקנים לחשבון שלי')}`);
  });

  it('should follow the payments placeholder note when an address is set', () => {
    vi.stubEnv('VITE_SUPPORT_EMAIL', 'support@vie.ad');

    renderIn(<PaymentsPlaceholderNotice id="note" />);

    expect(screen.getByTestId('payments-placeholder')).toHaveTextContent(
      "Payments aren't live yet — tokens are added by the team. Email the team: support@vie.ad",
    );
  });

  it('should leave the payments placeholder note alone without an address', () => {
    vi.stubEnv('VITE_SUPPORT_EMAIL', '');

    renderIn(<PaymentsPlaceholderNotice id="note" />);

    expect(screen.getByTestId('payments-placeholder')).toHaveTextContent(
      /^Payments aren't live yet — tokens are added by the team\.$/,
    );
  });
});
