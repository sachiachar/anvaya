# Anvaya: Release Notes

## Not yet released

### What's included

**Talking to Saathi (owner)**
- A gentle notice when the owner is close to, or has reached, today's allowance. Near the limit for spoken replies, replies stop being read aloud automatically, and the owner can turn it back on.

**Governance and the admin portal**
- Each person has a daily allowance, so one person can't use up capacity meant for everyone. Owners get 300,000 tokens, 30 voice minutes and 50 spoken replies a day; family members get 200,000 tokens. Use is counted on the server for each signed-in account.
- Certificate checks and the guardian never count against anyone's allowance, so checking a certificate always works.
- An approved request in the admin portal raises a person's allowance until it expires. Allowances reset each day.
- A live call ends when the person's voice minutes for the day run out. Two calls at the same time can't each use the full allowance.

## Prototype 0.1 · 28 September 2026 (AI Builder Cup 2026)

The first working prototype, built on Google Gemini and Google Cloud. See [architecture.md](architecture.md) for how it works and [use-cases.md](use-cases.md) for the full list of use cases.

### What's included

**Talking to Saathi (owner)**
- Hands-free live voice conversation: tap 🎤 once, talk naturally and interrupt at any time, with live captions (Gemini Live).
- Typing, photos and documents in the same conversation; policy and statement photos become account cards, with numbers masked.
- Natural spoken replies to typed messages (Gemini text-to-speech), with automatic fallback between voice models.
- One consistent companion: the same persona, language matching and honesty rules in chat and live calls.

**Keeping what matters**
- Suggestion cards for stories, advice, wishes, accounts and family members, with Keep, Not now and Keep all.
- Repeats are merged; follow-up details become Update cards that add to what's saved instead of replacing it.
- The family list is built from the conversation; Saathi asks who new people are.

**Next steps**
- A readiness score with up to 5 next steps, each with an action: talk about it, legal help, open the vault, or concrete steps for a bank or insurer.
- Steps stay until done or marked not relevant, and never come back reworded.

**Legal help**
- Flags wishes that need a legal document; sends a pre-filled summary to a legal partner (mocked) with the owner's consent.

**Secure vault**
- Secrets encrypted on the owner's phone; neither Anvaya nor the AI can read them.
- The owner chooses and invites the recipient; the recipient accepts; the owner confirms a matching key code before sealing.
- Recovery code for new or lost phones; a new key can be issued while the owner is alive.

**Handover**
- Death certificate and proof of authority pre-checked by AI, with a clear verdict and likelihood.
- Two different reviewers; a written reason is required to override the AI's "not valid"; audit log.
- Waiting period in which the owner can cancel with one tap.
- The recipient unlocks the vault on their own phone, asks Saathi questions (answers with sources, "never recorded" when unknown), and gets a claims checklist with draft letters.

**Family access**
- Unlisted family members can request access; the owner decides while alive, reviewers after a death.

**Access**
- Sign in with Google; only invited emails (or whole domains) can use the demo, checked on the server.

### Known limitations
- No per-role permissions yet; one signed-in browser plays every role in the demo.
- No notifications (push, SMS, email); the owner sees alerts only inside the app.
- The legal partner is mocked.
- Live calls end after about 15 minutes, and spoken replies can't be rule-checked like typed ones.
- Free-tier Gemini keys run out quickly; turn on billing for demos.

### Next
Sign-in and roles · notifications and reminders · a real legal partner · email and chat import · keeping the real recording of a story (opt-in) · voice letters and sealed letters for future dates · per-person family access to the archive · a pilot with a bank or insurer.
