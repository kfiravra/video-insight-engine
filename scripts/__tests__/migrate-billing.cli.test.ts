import { describe, it, expect } from 'vitest';
import { auditIdFor, looksLikeProd, parseCli, unconsumedMinutes } from '../migrate-billing.js';
import { compareKeepList } from '../migrate-billing-plan.js';
import { DAY } from './migrate-billing.fixtures.js';

describe('unconsumedMinutes', () => {
  it.each([
    [5, 1, 120],
    [2, 5, 0],
    [2, 2, 0],
    [0.1, 0, 3],
    [1.1, 0, 33],
    [2.3, 0, 69],
    [1, -4, 30],
    [0.03, 0, 0],
  ])('should turn $%d of credit after $%d of spend into %d minutes', (credit, spend, minutes) => {
    expect(unconsumedMinutes(credit, spend)).toBe(minutes);
  });
});

describe('auditIdFor', () => {
  it('should be a 24-char hex id that depends only on the user and the step', () => {
    expect(auditIdFor('6a7b2f6abacdb32871507996', 'plan')).toMatch(/^[0-9a-f]{24}$/);
    expect(auditIdFor('6a7b2f6abacdb32871507996', 'plan')).toBe(auditIdFor('6a7b2f6abacdb32871507996', 'plan'));
  });

  it('should differ per step and per user', () => {
    const ids = new Set([
      auditIdFor('6a7b2f6abacdb32871507996', 'plan'),
      auditIdFor('6a7b2f6abacdb32871507996', 'team-lot'),
      auditIdFor('6a7b2f6abacdb32871507997', 'plan'),
    ]);
    expect(ids.size).toBe(3);
  });
});

describe('looksLikeProd', () => {
  it.each([
    ['mongodb://localhost:27017/video-insight-engine?directConnection=true', false],
    ['mongodb://127.0.0.1:27017/x', false],
    ['mongodb://vie-mongodb:27017/video-insight-engine', false],
    ['mongodb://vieadmin:secret@vie-mongodb:27017/video-insight-engine?authSource=admin', true],
    ['mongodb://db.example.com:27017/x', true],
    ['mongodb+srv://cluster0.example.mongodb.net/x', true],
    ['not a uri', true],
  ])('should say %s is prod: %s', (uri, prod) => {
    expect(looksLikeProd(uri)).toBe(prod);
  });
});

describe('parseCli', () => {
  const NOW_CLI = new Date('2026-11-02T10:00:00Z');
  const parse = (argv: string[], env: NodeJS.ProcessEnv = {}) => parseCli(argv, env, NOW_CLI);

  it('should refuse neither --dry-run nor --run, and both', () => {
    expect([parse([]), parse(['--dry-run', '--run'])]).toEqual([null, null]);
  });

  it('should default the day to today in UTC and the admins to ADMIN_EMAILS', () => {
    const cli = parse(['--dry-run'], { ADMIN_EMAILS: 'A@example.com, b@example.com' });

    expect(cli?.options).toMatchObject({ dryRun: true, day: DAY, adminEmails: ['a@example.com', 'b@example.com'] });
  });

  it('should take the demo account from DEMO_USER_EMAIL and the eval accounts from the flag', () => {
    const cli = parse(['--run', '--eval-emails', 'e1@example.com,e2@example.com'], { DEMO_USER_EMAIL: 'Demo@Example.com' });

    expect(cli?.options).toMatchObject({ dryRun: false, demoEmail: 'demo@example.com', evalEmails: ['e1@example.com', 'e2@example.com'] });
  });

  it('should reject a malformed day and an unknown flag', () => {
    expect([parse(['--run', '--day', '02/11/2026']), parse(['--run', '--wat'])]).toEqual([null, null]);
  });

  it('should read an empty --eval-emails, as the runbook passes when there are none, as no accounts', () => {
    expect(parse(['--run', '--eval-emails', ''])?.options.evalEmails).toEqual([]);
  });

  it('should carry --yes-prod', () => {
    expect(parse(['--run', '--yes-prod'])?.yesProd).toBe(true);
  });

  it('should keep the ADMIN_EMAILS list beside the keep-list even when --admin-emails overrides it', () => {
    const cli = parse(['--dry-run', '--admin-emails', 'typed@example.com'], { ADMIN_EMAILS: 'Env@Example.com' });

    expect(cli?.options).toMatchObject({ adminEmails: ['typed@example.com'], envAdminEmails: ['env@example.com'] });
  });
});

describe('compareKeepList', () => {
  it('should find no difference between equal lists', () => {
    expect(compareKeepList(['a@x.com', 'b@x.com'], ['b@x.com', 'a@x.com'])).toEqual({ onlyKept: [], onlyEnv: [], envEmpty: false });
  });

  it('should name the admin kept here that the next boot would demote', () => {
    expect(compareKeepList(['a@x.com', 'typo@x.com'], ['a@x.com']).onlyKept).toEqual(['typo@x.com']);
  });

  it('should name the admin on ADMIN_EMAILS that this script does not keep', () => {
    expect(compareKeepList(['a@x.com'], ['a@x.com', 'b@x.com']).onlyEnv).toEqual(['b@x.com']);
  });

  it('should flag an empty ADMIN_EMAILS', () => {
    expect(compareKeepList(['a@x.com'], []).envEmpty).toBe(true);
  });
});
