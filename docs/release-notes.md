# Anvaya: Release Notes

## Not yet released

### What's included

**Talking to Saathi (owner)**
- Spoken replies read out only what Saathi actually said, checked on the server. Playing a reply again, or a new reply with the same words, reuses the saved audio.
- Spoken replies are limited to 1,000 characters.
- A gentle notice when the owner is close to, or has reached, today's allowance. Near the limit for spoken replies, replies stop being read aloud automatically, and the owner can turn it back on.

**Keeping what matters**
- After the handover, the family's assistant reads everything Vikram kept, so it understands questions worded differently or asked in another language ("Where are Papa's documents?" finds the papers in the blue folder). Each answer names the kept items it came from.
- Cards can now say where an item's papers are kept, and who a property or wish is meant for. Kept items link to the people they name (a policy to its nominee, a story to the people in it), so the family's assistant and the knowledge map can follow them.
- Everything the owner keeps is saved as a small open-format document (Open Knowledge Format), with where it came from and that the owner confirmed it. Changing a kept item keeps the earlier version, marked as replaced. Long account numbers keep only their last 4 digits.
- Demo copies made before this change start again once, from the beginning, the next time they're opened.

**Next steps**
- Readiness, legal needs and the claims checklist are worked out again only when the owner's information has changed; otherwise the last result comes back straight away.

**Access**
- Vikram's daughter Priya is in his family from the start, so he can choose her as his vault recipient straight away. Only the person Vikram chose can accept; if he chose someone else, Priya's screen says so.
- Switch between the people in your demo: Vikram (owner), Priya (family), Anjali and Rahul (reviewers) and the platform admin. A banner always shows who you are acting as. The server checks it: only Vikram changes his vault, only Priya opens it after release, and only a reviewer confirms. One reviewer can't confirm both the death and the legal authority, even though you play both.
- After signing in for the first time, you can give an optional nickname, asked once. Only the Anvaya team sees it, to tell demo copies apart.
- Each person who signs in gets their own private copy of the demo. Nobody else can see or change it, and "Reset my demo" restores only your own copy, including its recordings.
- The app is now called Anvaya, at https://anvaya-saathi.web.app, with a new logo, icons and navy theme. The old address redirects there.

**Checking a certificate of passing**
- Reviewers now see the check sign by sign: for an Indian certificate of passing, it looks for eight known warning signs, such as a missing or mismatched QR code, a pasted signature, impossible dates or the wrong issuing office, and says for each whether it found it, ruled it out or couldn't tell.
- Any warning sign found means "Needs a closer look". The signs come from a fraud reference guide with cited sources, shown on the Fraud page of the admin portal.

**Finding fraud before it happens (admin portal)**
- "Dream now" on the Fraud page imagines new ways a fraudster could get around our safeguards, starting from known fraud tactics and what the guardian has seen. Each idea is checked against web pages found with Exa search, and a page counts only if the sentence it's quoted for is really there. It's then matched to the safeguard that would catch it, or shown as a gap.
- Live mode shows the run as it happens: which stage it's in, each step, and new scenarios as they're added. A reviewer can press Stop at any time, and every run has a fixed limit on model calls to keep costs down.
- "Research now" looks for new fraud tactics for a guide. Only tactics and scenarios a cited source confirms can be approved, and nothing joins a guide until a risk reviewer approves it. Ideas no source confirms fade after three runs.

**Knowledge map (demo)**
- "Open the knowledge map" (on Vikram's screen) opens a second window that shows his kept knowledge as a map, filling in live: each story, wish, account and person appears the moment he taps Keep, linked to the people it mentions, its nominee and its bank or insurer.
- People a story mentions but who aren't kept yet show as dotted circles, and an account or policy with no nominee is marked in red, so the gaps are easy to see.

**Saathi asks about what's missing**
- Saathi now knows what is missing from Vikram's plan and gently asks about the most important piece when the moment fits: one at a time, never pressing. It might be an account with no nominee, a wish that gives an account to someone other than its nominee, a flat nobody is named to receive, or someone whose relationship isn't known.
- "Missing pieces" on My plan lists them, most important first. Skip any you'd rather not answer, and Saathi won't ask about it again.
- Telling Saathi who the nominee of an account is now updates that account, rather than becoming a wish, even if you only name the bank ("my wife is the SBI nominee").

**Knowledge map, take two**
- The map now looks like Obsidian's graph view: a dark canvas where items settle into place, bigger circles for items with more links, scroll to zoom, drag to move, and hover to light up an item's links. It zooms to fit until you move it, and "Fit the whole map" brings that back.
- More connections: a wish or story now links to the people it names in its own words, including "my wife" or "my daughter", and to the accounts and banks it mentions. "Transfer of SBI account to my wife" links to the SBI account and to his wife.

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
  - every private demo copy, with when it was last used and today's allowance used; your own is marked, and a platform admin can restore any copy to the start;
  - an audit log of every decision.
- The portal works on a phone: a compact top bar, sections you swipe between, and tables that scroll inside their card. Every page has a Refresh button, and the agents table fits on a laptop screen.
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
- Live calls show their tokens in the admin portal, as well as their minutes. Before, every call showed 0 tokens.

**Documents**
- New concept document and two pitches: one for people and families, one for banks and insurers. They cite figures for India and Asia, and regulatory deadlines. Branded PDFs are included.
- A business model with costs and margins, a brand guide, a proposal for governing the agents, and the guardian layer and storage design in the architecture document.

### Known limitations
- One signed-in person plays everyone in their demo copy through the switcher; in a real service each person would sign in separately. The admin portal has its own roles.
- No notifications (push, SMS, email); the owner sees alerts only inside the app.
- The legal partner is mocked.
- Live calls end after about 15 minutes (Gemini Live limit), and spoken replies can't be rule-checked like typed ones.
- Free-tier Gemini keys run out quickly; turn on billing for demos.
- Spend, use and refused calls in the admin portal are always real. Spend appears only once Google Cloud billing has data; until then the portal says it isn't available yet.
- The admin portal shows only real data. Budget requests, guardian interventions, fraud scenarios and guides, and quota headroom stay empty until those features record them.
- Spend can't yet be split by agent or tenant on the bill; those show use instead.

### Next
Opening the demo to every judge, with a guided tour · "ask for more" in the app · judges acting as the platform admin in the portal · guardian checks at every step · a claim file and bank view · accounts checked at the source · kept knowledge with gentle questions about what's missing · measured AI quality.

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
