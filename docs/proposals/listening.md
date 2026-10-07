# Saathi listens: from what people say to safe next steps

**Status:** agreed 7 October 2026; not built yet · **Issues:** PR-13 (parent), PR-13.1 to PR-13.6 · **Owner review:** governance, the registry, the fraud guides, the admin portal · **Builds on:** kept knowledge (PR-7, `knowledge.py`), the gap check (PR-7.10, `gaps.py`), fraud foresight ([fraud-foresight.md](fraud-foresight.md))

## 1. In short

Saathi is there to listen to anything the person says and to help fulfil their wishes. It isn't there to work through a checklist.

- **Listen.** Every conversation is turned into simple statements: who or what, what about it, and to whom or what ("Vikram · wants to give · the Mysore flat · to Sunita").
- **Notice wishes.** A statement that says what the person wants is a **wish**. Saathi follows it up, gently, until it's understood, planned and done, or the person changes their mind.
- **Find out what a wish needs.** For each kept wish, Anvaya looks up what fulfilling it takes, with sources, and proposes next steps.
- **Never pursue a risky step.** If a next step could be part of a fraud, Saathi never suggests it, asks about it or helps with it. It points to the safe way instead.
- **Learn from fraud research.** Wishes give anonymous counts to fraud dreaming and research. What a reviewer approves there comes back as protective next steps, and as new steps Saathi will never pursue.

People still decide everything: the owner keeps each card, and a reviewer approves what fraud research finds.

## 2. What changes

| Today | Proposed |
| --- | --- |
| Capture sorts what was said into stories, advice, practical things, wishes, accounts and people. | Capture also returns **statements** (subject, predicate, object). Wishes come from statements whose predicate says what the person wants. |
| Saathi asks about fixed gaps (nominee, will, papers…) when the moment fits. | Saathi **follows the person**. It follows up their wishes. A fixed gap is raised only when it touches what they are talking about. |
| The readiness planner lists up to 5 gaps in one model call. | Next steps come from **each kept wish**, researched with sources. Readiness becomes **coverage**: how much of what matters is understood, planned and done. |
| Fraud cues come only from the shape of kept items ("a policy with no nominee"). | Cues also come from **wishes** ("wants a property to go to someone, no will kept"). Counts only, as today. |
| No fraud finding reaches an owner. | Approved findings become **protective steps** and **steps never pursued**. |

## 3. Listening: statements

Each exchange (typed chat or a live call) returns statements next to today's cards.

| Part | What it is | Example |
| --- | --- | --- |
| Subject | A person, an asset or a place. Code links it to a kept item when it can, using the same matching as today's links (`knowledge._resolve`). | Vikram |
| Predicate | One from a **closed list in code**, or `other` with the words kept. | wants to give |
| Object | A person, an asset, a place or a short phrase. Linked the same way. | the Mysore flat → Sunita |
| Words | The person's own words, verbatim. | "The flat should go to Sunita." |
| Unsure | Whether they said they weren't sure. | no |

**The predicate list** (closed; the model picks one, code rejects anything else as `other`):

- **Facts:** owns, holds at (an institution), nominee is, papers kept at, related to, knows about, owes, is owed.
- **Wishes (intents):** wants to give, wants done, wants the family to know, wants to change, wants to do (a wish for their own life, such as visiting Varanasi with Sunita), promised to.
- **Feelings:** worries about, is proud of.

Statements ride on the card they came from, so the owner keeps one card, not one per statement (the feedback from #94). Nothing is kept until the owner taps Keep (AGENTS.md rule 3). A wish predicate makes the card a wish, decided in code, and a follow-up never turns a wish back into a story. A kept statement becomes links in the kept-knowledge graph. `intended_for` already exists; the new predicates add `wants`, `promised_to` and `worries_about` links.

## 4. Wishes and follow-ups

A kept statement with a wish predicate becomes a **wish** with a simple state:

`heard → kept → understood → planned → done` (or `changed` or `dropped` by the owner)

- **Understood** means Saathi knows enough to look into it: who, what and, for an asset, which one.
- **Follow-ups use the reminder timing that already exists** (`gaps.REMINDERS`): never twice within 30 minutes, at most one at a time, slower after "not now", and never again after "skip".
- **Saathi listens first.** It follows up a wish only when the conversation allows, never in the middle of a story.
- **The fixed gap rules stay as a safety net.** They protect the family even when nobody talks about them. But Saathi raises one only when it touches what the person is talking about, for example a nominee question when they mention that policy.

## 5. Finding out what a wish needs

A new registered agent, **`wish.planner`** (autonomy: propose; tool: `web_search`), looks into one kept wish at a time.

- **What it returns:** next steps, each **typed** (section 6) and each with its sources. It also says why the step helps, in plain words.
- **What it searches with:** code builds the search from typed fields only: the predicate, the asset type and the country, for example "transfer residential property to spouse India will". It never uses names, numbers or the person's words.
- **Statements are checked:** the existing web search keeps only statements whose quotes really appear on the page (`web_search.quoted`).
- **When it runs:** only when a wish is kept or changes. The result is cached, like readiness today.
- **No legal advice:** steps say what is usually done and who can do it, never what the law requires for this person.

**Readiness becomes coverage.** The score counts wishes that are understood, planned and done, plus the key facts a family needs (nominees, papers, a vault recipient). This replaces the readiness planner's single model call, which is retired. It also matches the "link coverage" idea in [architecture §5.1](../architecture.md).

## 6. Never pursue a risky step

**If a next step could be part of a fraud, Saathi never pursues it.** It doesn't suggest it, ask about it, remind the person about it or help with it. This is checked in code; the prompt only explains it.

**Steps are typed.** Each step has fields from closed lists in code:

- **What it does:** tell the family, record with the institution, make or update a legal document, keep the papers somewhere, add to the vault, gather information, talk more, plan with the family.
- **Channel:** the institution's official channel, a registered professional, a family member the owner named, the owner themselves, or the Anvaya app.
- **Flags:** involves a payment, involves a secret (an OTP, a password or a PIN), started by someone else, has a deadline.

**The check, in this order:**

1. **A step of an unknown type or channel is not pursued.** When unsure, the answer is no.
2. **A step that involves a secret is never pursued.** Nobody needs an OTP or a password to fulfil a wish.
3. **A step that matches an approved tactic is not pursued.** Each approved tactic in the fraud guides also lists the step types, channels and flags it exploits. A reviewer tags these in the portal when approving it. Examples: paying a fee to "release" a claim, signing through an agent, changing a nominee or bank details through someone who called, moving money to a "safe" account.

**What Saathi does instead.** It keeps listening to the wish itself, because the wish isn't the problem, only that route. If the person brings the route up, Saathi says there's a safer way and names it: "The insurer can record that directly", "A registered lawyer can draw that up". It never names the fraud and never accuses anyone, family included.

**When the risk is in the wish.** Some things sound like pressure: someone else started it ("my nephew says I should sign the flat over now"), there's a rush, or someone asks for an OTP. Saathi doesn't pursue any step for that wish and doesn't take sides. It gently suggests talking to someone they trust, or the official route. Code records a count only (section 7).

**A record for oversight.** Each step that isn't pursued leaves a guardian event with no words and no names: the rule or tactic that stopped it, the owner's hashed key, and the time. The portal shows these as counts, so the team can see the check working.

## 7. Fraud cues from wishes

The fixed-code cue extractor (`fraud_cues.py`) gets new patterns from kept wishes and pressure signals, always as counts with the same minimum and rounding as today:

- `wish.property_to_person.no_will`: wants a property to go to someone, and no will is kept;
- `wish.nominee_change.not_done`: wants to change a nominee, and the step isn't done;
- `wish.informal_promise`: has promised money or an item, with nothing written down;
- `pressure.started_by_other`: a wish someone else started;
- `pressure.secret_asked`: someone asked for an OTP or a password.

Dreaming and research use these as starting points, as they use today's cues. The fraud steps still never read a bundle.

## 8. Fraud findings back into next steps

This is [fraud-foresight §8](fraud-foresight.md) made concrete.

- **A protective step.** When a reviewer approves a scenario linked to a pattern, they also approve one protective step for it, worded calmly: for example, "Ask the insurer to record the nominee; a promise isn't enough". A wish plan that matches the pattern includes that step and ranks it higher.
- **A step never pursued.** The tactic's tags (section 6) take effect for every owner at once.
- **People decide.** A ranking change beyond one step needs a person in the portal (fraud-foresight §7). Nothing loosens a check without a person.

## 9. Rules enforced in code

Each rule has a test.

1. Predicates, step types, channels and flags come from closed lists in code. Anything else is `other` and is never acted on.
2. Nothing is kept without the owner's Keep. A wish is planned only once it's kept.
3. A step of an unknown type, a step with a secret, or a step matching an approved tactic never reaches the owner, Saathi's instructions or the suggested topic.
4. Searches are built from typed fields only, with no names, numbers or the person's words.
5. Fraud steps get counts only, at or above the minimum, rounded down.
6. Guardian events about steps carry no words and no names.
7. Nothing Saathi says to the owner talks about the owner's death (`tone.py`, #139).

## 10. Privacy and governance

- **Registry:** `wish.planner` is new (propose, `web_search`, with limits on calls per wish and per day). `capture.extractor` and `saathi.chat` return statements, with the same models. Both need rows in [agent-governance.md](agent-governance.md).
- **Cost:** research runs per kept wish, not per conversation, and is cached. Allowances, the gateway and the kill switch apply as today.
- **The portal** gets the step tags on tactics (section 6), the protective step on approved scenarios (section 8) and the count of steps not pursued.
- **Overlap with the fraud review (#137):** this touches `fraud_cues.py` and `fraud_guides.py`, which FRD-1 to FRD-8 also change. Section 7 waits for FRD-1. Step tags on tactics are built after FRD-6, so a single reviewer can't change them alone.

## 11. Work plan

| Item | What | Depends on |
| --- | --- | --- |
| PR-13.1 | Statements and wishes in capture, kept as links | none |
| PR-13.2 | Saathi listens first: wish follow-ups; fixed gaps only when relevant | PR-13.1 |
| PR-13.3 | Never pursue a risky step: typed steps and the check (section 6) | none |
| PR-13.4 | Researching a wish: `wish.planner`, next steps from wishes, readiness as coverage | PR-13.1, PR-13.3 |
| PR-13.5 | Fraud cues from wishes and pressure signals | PR-13.1, FRD-1 |
| PR-13.6 | Approved findings become protective steps and step tags | PR-13.3, PR-13.5, FRD-6 |

PR-13.3 ships before or with PR-13.4: no researched step reaches anyone without the check.

## 12. How we check it

- **The demo:** Vikram says "I want the Mysore flat to go to Sunita". A wish card appears. He keeps it, and next steps follow with sources: make a will through a registered lawyer, and tell Sunita where the papers are.
- **Pressure:** "My nephew says I should sign the flat over now". Saathi doesn't help with it, suggests a trusted person, and the portal shows one more step not pursued.
- **Fraud in the loop:** after a reviewer approves a scenario about informal promises, Vikram's plan includes the protective step. A step matching the tactic never appears.
- **Tests:** each rule in section 9.

## 13. Decisions made (7 October 2026)

1. **Predicates:** a closed list in code, with `other` keeping the words.
2. **Fixed gap rules:** kept as a safety net, raised only when relevant to what the person is saying.
3. **Wish research:** web search with sources, only for kept wishes, cached.
4. **Scope:** all six items in a simple form before 18 October.
5. **A step not pursued:** Saathi says there's a safer way and names it, never the fraud.
6. **Risky steps:** never pursued by Saathi, checked in code, unknown means no.
