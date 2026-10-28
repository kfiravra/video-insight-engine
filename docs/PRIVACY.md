# Privacy & Data Deletion

> User-facing summary of what we store and how to delete it. For the
> engineering runbook, see [`GDPR.md`](./GDPR.md).

## What we store

When you use Video Insight Engine, we keep:

| Data | Why | Where |
|---|---|---|
| Email + name | Sign-in identity | Our database |
| Encrypted password | Sign-in security | Our database (bcrypt-hashed) |
| Library entries (videos you've added) | So your library appears when you log in | Our database |
| Folders, playlists | Library organization | Our database |
| Video minutes (your plan, free video, minutes granted and used) | What you can summarise; accounting | Our database |
| AI cost usage (model, tokens, cost, time, and the account and request it came from) | Our own cost reporting (never charged to you in dollars) | Our database; the link to you is removed after 90 days (see below) |
| Saved chat notes | The "Save note" action in the chat assistant | Our database |

We **do not** store:
- Your YouTube history outside what you explicitly added.
- Your IP address beyond aggregated, hashed counters on shared pages.
- Card or billing details. No payments are processed yet; minutes are granted by the team.
- The text of your chat messages in our AI usage records. The chat assistant's prompts are never stored there.

## How to delete your account

1. Open **Settings → Account → Delete account**.
2. Confirm. Your account is immediately frozen — you'll be logged out and unable to sign in.
3. We give you **30 days** to change your mind. Contact `support@…` during that window if you want to restore it.
4. After 30 days, every piece of your personal data is permanently removed from our systems. You'll receive a confirmation that deletion completed.

### What happens technically

After the 30-day grace period:

- Your user record and all rows pointing at it (library, folders, notes, the old daily cost log) are deleted from our database.
- Your **minutes history** (the plan, minutes granted and used) is an accounting record, so it is kept, but **unlinked from you**: every reference to your account is replaced by a random id that points to no one.
- An **audit row** is written so we can prove the deletion happened. The row contains a one-way hash of your email — not the email itself — plus the deletion timestamp and counts of what was removed. This satisfies our compliance obligations without keeping your personal data.

## What is NOT deleted

Three categories of data are not removed because they aren't yours:

1. **Video summaries** themselves (the AI-generated output) live in a shared cache. They're keyed by the YouTube video, not by the user. Removing them would break the same video for every other user who has it.
2. **Anonymous engagement signals** on public share pages (view counts, like counts) — these are stored as one-way hashes of IP addresses, never linked to your account.
3. **AI usage records** (model, tokens, cost, time) are kept as cost analytics. Chat prompts are never stored in them. For other AI calls, such as summarising a video, the first 200 characters of the prompt are kept for 90 days. After 90 days we remove the account link (`user_id`), the request id (`request_id`) and the prompt preview (`prompt_preview`) from every record, so what is left contains no personal data.

If you specifically need every trace of a particular video removed, contact support.

## Billing data

No payments are processed yet; minutes are granted by the team. We hold no card details and no payment-provider identifier, so there is nothing of that kind to remove when you delete your account.

## Data export

GDPR Article 20 (data portability) is being scoped separately. Until that ships, email `support@…` with a written request and we'll generate a JSON archive of your library and notes within 30 days.

## Contact

Privacy questions or formal Article 17 / Article 20 requests: `privacy@…`
