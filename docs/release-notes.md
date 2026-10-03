# Anvaya: Release Notes

## Not yet released

### What's included

**Talking to Saathi (owner)**
- Spoken replies read out only what Saathi actually said, checked on the server. Playing a reply again, or a new reply with the same words, reuses the saved audio.
- Spoken replies are limited to 1,000 characters.
- A gentle notice when the owner is close to, or has reached, today's allowance. Near the limit for spoken replies, replies stop being read aloud automatically, and the owner can turn it back on.

**Next steps**
- Readiness, legal needs and the claims checklist are worked out again only when the owner's information has changed; otherwise the last result comes back straight away.

**Access**
- The app is now called Anvaya, at https://anvaya-saathi.web.app, with a new logo, icons and navy theme. The old address redirects there.

**Governance and the admin portal**
- A separate platform admin portal for the Anvaya team, at https://anvaya-admin.web.app. It shows:
  - actual spend against the monthly budget, with a forecast, in US dollars, read from Google Cloud billing (up to yesterday);
  - spend by Google Cloud service and the largest items on the bill;
  - use by agent, model and tenant (calls, tokens and voice minutes);
  - the register of agents, with each one's role, allowed models and tools, and use;
  - calls that were refused;
  - budget requests to approve or reject;
  - guardian interventions to confirm or reverse;
  - the fraud coverage map and reference guides;
  - judges' demo copies;
  - an audit log of every decision.
- Roles in the portal (platform admin, budget approver, risk reviewer, viewer) are checked on the server. Anyone else who signs in can only view.
- The portal never shows conversation text or names. People appear as anonymous keys, and counts under 5 are hidden.
- Every call to Gemini is checked against the register of agents: the agent must be registered and the model allowed. Refused calls are recorded, and each call's tokens are counted.
- A kill switch in the portal pauses voice and spoken replies first, then everything except certificate checks and the guardian.
- Limits for each agent:
  - oversized requests are refused before anything is spent;
  - each reply has a length cap;
  - a request is retried at most twice;
  - the family's assistant makes at most 6 model calls per question;
  - live calls end at 20 minutes.
- Each person has a daily allowance, so one person can't use up capacity meant for everyone. Owners get 300,000 tokens, 30 voice minutes and 50 spoken replies a day; family members get 200,000 tokens. Use is counted on the server for each signed-in account.
- Certificate checks and the guardian never count against anyone's allowance, so checking a certificate always works.
- An approved request in the admin portal raises a person's allowance until it expires. Allowances reset each day.
- A live call ends when the person's voice minutes for the day run out. Two calls at the same time can't each use the full allowance.

**Documents**
- New concept document and two pitches: one for people and families, one for banks and insurers. They cite figures for India and Asia, and regulatory deadlines. Branded PDFs are included.
- A business model with costs and margins, a brand guide, a proposal for governing the agents, and the guardian layer and storage design in the architecture document.

### Known limitations
- No per-role permissions in the app yet; one signed-in browser plays every role in the demo. Anyone signed in can see the demo family's data until private demos per person arrive. The admin portal has its own roles.
- No notifications (push, SMS, email); the owner sees alerts only inside the app.
- The legal partner is mocked.
- Live calls end after about 15 minutes (Gemini Live limit), and spoken replies can't be rule-checked like typed ones.
- Free-tier Gemini keys run out quickly; turn on billing for demos.
- Spend, use and refused calls in the admin portal are always real. Spend appears only once Google Cloud billing has data; until then the portal says it isn't available yet.
- Budget requests, guardian interventions, fraud scenarios and guides, judges' demo copies and quota headroom are clearly labelled examples until those features are built.
- Spend can't yet be split by agent or tenant on the bill; those show use instead.

### Next
A private demo for each judge · guardian checks at every step · a claim file and bank view · accounts checked at the source · kept knowledge with gentle questions about what's missing · measured AI quality.

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
