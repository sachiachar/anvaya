# Anvaya: Release Notes

## Not yet released

### What's included

**The new design (preview)**
- A new design of the app, in the brand's own navy, gold and mist with the Lora and Inter typefaces, opens at https://anvaya-saathi.web.app/v2.html. "Try the new design" on the current app's header goes there, and "Switch to the classic design" comes back. Both show the same demo copy, and the current app works as before.
- This first step brings the look, sign-in, and the owner's new tabs: Talk, Kept, Family and Vault. The tabs fill in over the next updates; until then each one links to the classic design.
- **Talk, in the new design:** one screen from hello to saved, in the same light look throughout. Vikram's home is a greeting, a suggested topic ("Today, perhaps") and a large Talk to Saathi button. Tap it and the microphone goes live in place: gold rings move with his voice, a clock and Mute and End sit under it, and the conversation appears underneath as it happens.
- Nothing asks for a decision during the conversation. When it ends, Saathi sums it up in a few sentences, and everything it picked up is listed, already marked to save. Vikram removes only what he doesn't want, then saves the rest with one tap. He can also decide later, or discard the whole conversation, which deletes what was said and everything from it still waiting. Things kept before are never touched.
- Typing and photos use the same screen and the same review: "Review and save" sits above the text box.
- The top of a call keeps one size whatever Saathi is doing, so nothing moves up or down as the status changes. After a call, "Gathering what you said" never takes more than about 12 seconds, with "Review now" after 4; anything Saathi is still finishing waits as "from earlier". The server also finishes saving a call before its connection ends, and a save that never finishes stops holding anything up after two minutes.
- During a call the screen holds still: the microphone, Mute and End call stay in place, and only the conversation scrolls, under them. It follows the newest words unless Vikram scrolls up to read something.
- Near or at today's limit, "Ask for more" works in the new design too, with the same limits, and shows when a request is waiting and when it's approved.
- **Kept, in the new design:** everything Vikram has kept is "The book of Vikram Rao", for the family he names, with chapters for stories and advice, money matters, wishes and people. He can remove a page after a confirmation, write a wish, and check which wishes need a legal document. Gaps are "pages still to write", each with Talk about this or Skip, instead of a score out of 100.
- **Family:** choosing who receives the vault is four clear steps (choose, they accept, the codes match, seal), and requests to see his stories wait for his Allow or Decline.
- **Vault:** it says plainly whether it's sealed and for whom. New secrets are locked on the phone before they leave it, and the list shows only what each secret is, never its locked text.
- **Priya, in the new design:** one stage at a time. She accepts the vault on her own phone and keeps a recovery code, matches the code with Vikram, and can set up a new phone with the code. "When the time comes" explains the steps before asking for the certificate. The handover is a tracker in plain words, with the waiting period counting down. After release she opens the vault on her phone, asks about Vikram, and prepares the claim letters (each with Copy).
- **Our team, in the new design:** each check sign by sign, Confirm for the loss and for the legal authority by two different reviewers, and a written reason, typed in the page, to go against a "not valid" check. The audit log is under each case.
- Everything that's only for the demo now sits in one Demo drawer: whose screen to show, "Show how Saathi works", the optional nickname, the knowledge map and "Reset my demo", which now asks before it clears anything.
- If someone reports that the owner has passed away, the alert with "I'm well, cancel this" shows in the new design too.
- The fonts are served from Anvaya's own site, so no visitor's address goes to a font service. Hindi and other Devanagari text has its own matching typeface.

**Your own model key (optional)**
- Anyone can add their own Gemini key in the new design's Demo menu ("Your own model key"): a Google AI Studio key, free or paid, or a Vertex AI express-mode key. Everything they do in the demo then runs on their key first, and on Anvaya's only if theirs fails, so the demo keeps working when Anvaya's free quota runs out. Without a key, nothing changes.
- **Test** shows what works with the key (typing, the family's questions, voice calls, spoken replies, fraud research) before **Save**. Saving tests it again.
- The key is stored encrypted and is never shown again, to its owner or anyone else: only its last 4 characters. Nobody else's calls ever use it.
- Calls on your own key don't count against today's allowance.
- A new **Model keys** page in the admin portal shows whose key answered each call, failovers, and each person's key status. Platform admins can turn the feature off or limit the kinds of key, for everyone or one person, test a saved key again, or remove it, with a reason that's logged. They never see a key.

**Talking to Saathi (owner)**
- Spoken replies read out only what Saathi actually said, checked on the server. Playing a reply again, or a new reply with the same words, reuses the saved audio.
- Spoken replies are limited to 1,000 characters.
- Live calls keep flowing while Saathi saves what was said. Requests answer faster, because sign-in no longer waits on Google each time, and the server starts up faster.
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
- "Dream now" also starts from gaps common in what owners have kept, such as a policy with no nominee: only counts of five or more owners (a platform admin can raise this on the Fraud page, never lower it), never who or what. A scenario shows the gap it starts from. Owners see one line about it under "Missing pieces": "We count common gaps, never the details, to protect everyone." The demo includes twelve clearly labelled synthetic owners so the counts are there.
- Live mode shows the run as it happens: which stage it's in, each step, and new scenarios as they're added. A reviewer can press Stop at any time, and every run has a fixed limit on model calls to keep costs down.
- "Research now" looks for new fraud tactics for a guide. Only tactics and scenarios a cited source confirms can be approved, and nothing joins a guide until a risk reviewer approves it. Ideas no source confirms fade after three runs.

**Knowledge map (demo)**
- "Open the knowledge map" (on Vikram's screen) opens a second window that shows his kept knowledge as a map, filling in live: each story, wish, account and person appears the moment he taps Keep, linked to the people it mentions, its nominee and its bank or insurer.
- People a story mentions but who aren't kept yet show as dotted circles, and an account or policy with no nominee is marked in red, so the gaps are easy to see.
- It looks like Obsidian's graph view: a dark canvas where items settle into place, bigger circles for items with more links, scroll to zoom, drag to move, and hover to light up an item's links. It zooms to fit until you move it, and "Fit the whole map" brings that back.
- A wish or story links to the people it names in its own words, including "my wife" or "my daughter", and to the accounts and banks it mentions. "Transfer of SBI account to my wife" links to the SBI account and to his wife.
- Someone related through another person now hangs off that person: "Kavya, wife of my brother-in-law" links to Bhaskar, not straight to Vikram. "Bhaskar's son" works the same way.

**Saathi asks about what's missing**
- Saathi now knows what is missing from Vikram's plan and gently asks about the most important piece when the moment fits: one at a time, never pressing. It might be an account with no nominee, a wish that gives an account to someone other than its nominee, a flat nobody is named to receive, or someone whose relationship isn't known.
- "Missing pieces" on My plan lists them, most important first. Skip any you'd rather not answer, and Saathi won't ask about it again.
- Telling Saathi who the nominee of an account is now updates that account, rather than becoming a wish, even if you only name the bank ("my wife is the SBI nominee").

**Removing something recorded wrongly**
- "What you've kept" on My plan lists every story, wish, account, policy and person Vikram has kept, with Remove. The knowledge map's side panel has the same button.
- Removing deletes the item and every earlier version of it from the kept knowledge for good, along with any update cards waiting for it: the family and their assistant never see it. The record of changes keeps only "An item was removed by the owner". The conversation it came from stays in the chat.
- Nothing can be removed while a handover is being reviewed, and the person who receives the vault can't be removed until Vikram chooses someone else.

**Updating a story or wish without duplicating it**
- When Saathi words a story or wish differently from one Vikram already kept ("SBI account for my wife" after "Transfer of SBI account"), the card says "This looks like an update to …" and asks: Replace it, Keep both, or Not now. Replace it keeps the new card as the next version, so the map shows one item, not two. Nothing is merged without asking.

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
- Near or at today's limit, "Ask for more" in the app sends a request with a reason (up to 300,000 more tokens, 60 voice minutes or 100 spoken replies, for up to 72 hours). The app shows when it's waiting and when it's approved.
- Each judge is the platform admin of their own demo copy in the admin portal: they can approve or reject their own copy's requests, and see only their own copy and decisions. Everything else in the portal is read-only for them, and the kill switch and settings stay with the Anvaya team.
- A live call ends when the person's voice minutes for the day run out. Two calls at the same time can't each use the full allowance.
- Live calls show their tokens in the admin portal, as well as their minutes. Before, every call showed 0 tokens.

**Documents**
- New concept document and two pitches: one for people and families, one for banks and insurers. They cite figures for India and Asia, and regulatory deadlines. Branded PDFs are included.
- A business model with costs and margins, a brand guide, a proposal for governing the agents, a proposal for finding fraud before it happens ("fraud foresight"), and the guardian layer and storage design in the architecture document.

### Known limitations
- One signed-in person plays everyone in their demo copy through the switcher; in a real service each person would sign in separately. The admin portal has its own roles.
- No notifications (push, SMS, email); the owner sees alerts only inside the app.
- The legal partner is mocked.
- Live calls end after about 15 minutes (Gemini Live limit), and spoken replies can't be rule-checked like typed ones.
- Free-tier Gemini keys run out quickly; turn on billing for demos.
- Spend, use and refused calls in the admin portal are always real. Spend appears only once Google Cloud billing has data; until then the portal says it isn't available yet.
- The admin portal shows only real data. Guardian interventions stay empty until the guardian core is built, and quota headroom until it's read from Cloud Monitoring.
- Fraud research and the checks behind "Dream now" need an Exa key (`EXA_API_KEY`); without it, a run says web search isn't set up yet. Imagined scenarios no source confirms can only be discarded, and fade after three runs.
- Spend can't yet be split by agent or tenant on the bill; those show use instead.

### Next
Opening the demo to every judge, with a guided tour · guardian checks at every step · a claim file and bank view · accounts checked at the source · measured AI quality.

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
