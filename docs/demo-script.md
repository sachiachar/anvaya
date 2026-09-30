# Anvaya: Demo Script

*A 3-minute demo video, with the lines to say to Saathi. Say them as written; natural pauses are fine.*

## Before you record

- [ ] Use headphones, so Saathi doesn't hear itself.
- [ ] Sign in at the live site with an allowed Google account, then tap **Reset demo**.
- [ ] Open a second browser profile (or a phone) for **Priya**, so the key-code match looks real.
- [ ] Keep a sample death certificate image and a "letters testamentary" image ready (any clearly labelled sample document).
- [ ] Turn off **Show how the AI works**, except for the AI-first moment in scene 3.
- [ ] Billing on for the Gemini key (free-tier limits can stop mid-take).

**On screen, in brackets:** what to tap. **Narration:** your voice-over for the video. **Say:** what you speak to Saathi as Vikram.

---

## Scene 1: The problem (0:00–0:15)

**Narration:** "When someone dies suddenly, their family can't find the money. In India alone, over ₹72,000 crore sits in unclaimed bank deposits. Nobody knew the accounts existed, or who the nominee was. Anvaya fixes that by letting you just talk."

[Show the sign-in screen with the logo, then sign in.]

---

## Scene 2: Vikram talks to Saathi (0:15–1:15)

[**Vikram** tab → **Talk** → tap 🎤. Wait for "Listening…".]

**Say:**
> "Hello Saathi. I'm Vikram. My wife is Meena, and we have two children, Priya and Arjun."

*Expect:* a warm reply that calls you "Vikram ji" and asks a follow-up. Family cards for Meena, Priya and Arjun.

**Say:**
> "My first job was at a textile mill in Mysore in 1987. I gave my whole first salary to my mother."

*Expect:* a question about your mother. A **Story** card.

**Say** (interrupt while Saathi is still talking, to show it's a real conversation):
> "Sorry, one more thing: I have an LIC Jeevan Anand policy, number ending 4492."

*Expect:* Saathi stops mid-sentence, then asks who the nominee is. An **LIC ••4492** card, "Missing: nominee".

**Say:**
> "Please make Ajay the nominee."

*Expect:* a reminder that the change must also be submitted to LIC, and "Who is Ajay?"

**Say:**
> "Ajay is my nephew. And I want our flat in Mysore to go to Meena, and after her, to both children equally."

*Expect:* a **Family: Ajay (nephew)** card and a **Wish** card for the flat.

[Tap **End**. Tap **Keep all** on the cards.]

**Narration:** "No forms. Saathi listens, asks gentle questions, and suggests what to keep. Nothing is saved until Vikram says yes."

---

## Scene 3: Next steps and the vault (1:15–1:55)

[**My plan** → **Next steps**. Turn on **Show how the AI works** for a few seconds, then off.]

**Narration:** "Saathi checks what the family would still be missing, and turns it into next steps: a will for the flat, updating the LIC nominee with the exact steps. This is AI-first: the AI runs the loop, and people make the decisions."

[Scroll to **Secure vault** → choose **Priya (daughter)** → **Invite**.]

[Switch to Priya's browser → **Priya** tab → **Accept as Priya** → show the recovery code → tick the box.]

[Back to Vikram → the approval card shows a key code → show that it matches Priya's screen → **Approve** → **Seal vault**. Add a secret: label "Email login", secret "demo-only" → **Encrypt and save**.]

**Narration:** "Passwords are locked on Vikram's phone. Only Priya can open them, and only after careful checks. Not even Anvaya or the AI can read them."

---

## Scene 4: The handover (1:55–2:50)

[**Priya** → **Start the handover** → upload the sample death certificate.]

[**Our team** → type reviewer name "Asha" → show the AI verdict → **Confirm death** (if it says "Not a valid document", show that and give a reason: "Original certificate verified at the branch").]

[**Priya** → upload the sample proof of authority. **Our team** → change reviewer to "Ravi" → **Confirm legal authority**.]

**Narration:** "The AI pre-checks every document, but people decide. Two different reviewers, and a waiting period in which Vikram can cancel with one tap if he's alive."

[Wait for the waiting period to end (60 s in the demo; speed this up in editing). **Priya** → **Unlock**: "Email login: demo-only" appears.]

[**Ask about Papa**: type]
> "What insurance did Papa have?"

*Expect:* the LIC Jeevan Anand policy ••4492, nominee Ajay, with sources.

[Type]
> "Did Papa have any crypto?"

*Expect:* "He never recorded anything about that." (it doesn't invent answers)

[**Prepare checklist and letters**.]

**Narration:** "Priya gets answers in her father's own words, a claims checklist, and draft letters. Nothing lost, nothing invented."

---

## Scene 5: Close (2:50–3:00)

[Show the logo.]

**Narration:** "Anvaya, powered by Saathi. Your stories, your wishes, and everything your family will need. Built on Gemini and Google Cloud."

---

## If something goes wrong

| Problem | Quick fix |
| --- | --- |
| Saathi doesn't answer on the live call | Tap **End**, then 🎤 again; check headphones and microphone permission |
| "Usage limit reached" or no voice | The free-tier quota is used up: turn on billing, or type instead (replies still appear) |
| A card appears twice or looks wrong | Tap **Not now**; say the detail again |
| "Please sign in again" | Sign out and in; check the email is on the allowlist (GitHub → production → `ALLOWED_EMAILS`) |
| The waiting period is too long for the take | Cut it in editing; it's 60 seconds in the demo |
| Want a clean start | **Reset demo** (top right) |

## Optional extra lines (for a longer demo)

- "My advice to my children: save money on the day your salary arrives, not at the end of the month." → an **Advice** card.
- "All my original papers are in the blue folder in the steel cupboard in our bedroom." → a **Practical** card. Then: "Meena has the key." → an **Update** card that adds to it, rather than a new one.
- "What's my bank password?" (on the live call) → Saathi refuses and points to the vault.
- "I also have an SBI savings account ending 1180. Meena is the nominee." → a separate **SBI** card (not merged with LIC).
