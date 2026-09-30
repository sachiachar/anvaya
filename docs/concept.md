# Anvaya

*Team Saathi · BFSI track · Revision 4, 30 September 2026*

*The full picture. The focused pitches are [pitch-individuals.md](pitch-individuals.md) and [pitch-institutions.md](pitch-institutions.md); costs and margins are in [business-model.md](business-model.md); how it is built is in [architecture.md](architecture.md); what is available in each version is in [release-notes.md](release-notes.md).*

*Anvaya (अन्वय) is Sanskrit for lineage and succession: the thread that connects one generation to the next. Its companion, Saathi, takes the everyday word for "companion" in each language; see [brand.md](brand.md).*

---

## Nobody plans to leave suddenly

We all mean to sit down one day and write it all out: the accounts, the policies, who should have the flat, where the papers are. We tell ourselves there is time.

And then, sometimes, there isn't.

A father goes for his morning walk and doesn't come home. A mother who ran the whole household is gone after a short illness. There is no goodbye, no conversation at the kitchen table, no folder left on the shelf. The family is still in shock, and within days they are facing questions nobody prepared them for:

- Which bank accounts, insurance policies and investments were there?
- Who is the nominee? Where are the papers?
- What did they want for the house, the savings, the watch?
- What are the passwords?

The person who knew every answer is the one person they can no longer ask.

## Anvaya, in one sentence

**Anvaya is a family succession service that anyone can download, and that banks and insurers can offer their customers: people talk with Anvaya's warm companion, Saathi, about their accounts, nominees and wishes while they are here; trusted partners help close the gaps; and when the time comes, the right family member receives a safe, fraud-checked handover and claims that are ready to process.**

## What is lost when nobody knows

Because families don't know what was there, a great deal of money is simply never claimed:

| Where | What is waiting to be claimed | As of |
| --- | --- | --- |
| **India** | ₹72,454 crore in bank deposits, moved to the Reserve Bank of India's Depositor Education and Awareness Fund after ten years untouched[^in-bank] | 28 Jan 2026 |
| | ₹8,974 crore in insurance, still with the insurers[^in-ins] | 28 Feb 2026 |
| | ₹3,749 crore in mutual funds[^in-ins] | 28 Feb 2026 |
| **Japan** | ¥129.2 billion in estates handed to the national treasury because no heir came forward, the highest since records began in 2013[^jp] | Financial year 2024 |
| **South Korea** | More than ₩10 trillion in unclaimed insurance money[^kr] | 2026 |
| **Malaysia** | RM13.3 billion with the Accountant General; only about RM4 billion has ever been claimed back since 1977[^my] | April 2025 |
| **Australia** | A$21.2 billion in lost and unclaimed superannuation, across about 7.5 million accounts[^au] | 30 Jun 2026 |

And it is the sudden losses that leave families least prepared. In Singapore, **2 in 5 CPF members lost before the age of 65 had named no nominee**, against about 1 in 5 overall. Without a nomination, their savings go to the Public Trustee, a fee is deducted, and it can take up to six months to work out who in the family may claim.[^sg]

These totals count all unclaimed money, not only what was left behind by someone who is gone. But every family who has lost someone suddenly knows how it happens: the one person who knew about the account is the one person they can no longer ask.

Most people never write any of this down: forms are tedious, and nobody likes to think about the day they won't be here.

### Banks and insurers carry the cost too

| For the institution | What it means |
| --- | --- |
| Settlement deadlines | In India, a 15-day clock with penalties when the delay is the institution's (see below); elsewhere, regulators are pushing for faster, simpler estate payouts |
| Incomplete files | Families don't know what to submit, so files go back and forth and the clock can't start |
| Missing or outdated nominees | Claims that need legal heirship papers, take months and generate complaints |
| Slow, paper-heavy claims | Staff check certificates and legal papers by hand, one case at a time |
| Unclaimed balances | Regulatory reporting, transfers to a regulator's fund, and scrutiny of how well the institution reunites money with families |
| Fraud | Forged certificates, false heirs, and people quietly using the accounts of a customer who is no longer here |
| Losing the next generation | Heirs who go through a painful claims experience take their money elsewhere |

### Why now

Regulators across Asia are making the family's claim faster, simpler and measured.

**In India** *(these deadlines and penalties are Indian rules)*

- **Banks** must settle claims on the deposit accounts of a customer who is no longer here within **15 calendar days** of receiving all required documents, in force by 31 March 2026. If the delay is the bank's, it pays interest of at least Bank Rate + 4%, or ₹5,000 a day for lockers.[^rbi-claims]
- **Life insurers** must settle within **15 days of intimation**, or 45 days where investigation is needed, or pay interest at bank rate + 2%.[^irdai]
- **Up to four nominees** per bank account are allowed from 1 November 2025.[^nominees]
- **Data protection** under the DPDP Rules arrives in phases, with full obligations from May 2027.[^dpdp]

**Across the rest of Asia** *(different rules, the same direction)*

- **Hong Kong:** the HKMA backed a practice (April 2025) that lets banks pay estate balances without waiting for the grant to be amended.[^hk]
- **South Korea:** since November 2024, life insurance payouts can be placed in trust and paid to family on a schedule the policyholder chose.[^kr-trust]
- **Japan:** heirs can ask one bank to locate accounts at other banks, where those accounts were linked to the family member's My Number.[^jp-mynumber]
- **Singapore:** without a CPF nomination, savings go to the Public Trustee, after a fee and up to six months to identify the family.[^sg]
- **Malaysia:** since August 2024, unclaimed money goes to government revenue after 10 years instead of 15.[^my-act]

A complete, pre-checked claim file is exactly what these rules reward. The detail is in [pitch-institutions.md](pitch-institutions.md#why-now).

## Our answer: a conversation, not a form

Anvaya makes getting your affairs in order as easy as **talking to someone you trust**, and it connects everything that follows.

1. **Talk with Saathi.** No forms. You talk with a warm companion the way you would with an old family friend, and Saathi quietly gathers everything your family will one day need. Nothing is kept until you say so.
2. **Close the gaps with trusted partners.** When something is missing, such as a will, a nominee or a safe place for original papers, Saathi points you to vetted partners in a **marketplace**, and you choose.
3. **A careful, fraud-resistant handover.** When the time comes, automated checks and two human reviewers confirm the official certificate and the family member's authority. The family gets their answers, and the bank or insurer receives a claim that has already been checked.

```mermaid
flowchart LR
    A["While you are here<br/>Talk with Saathi"] --> B["Close the gaps<br/>Partner marketplace"]
    B --> C["When the time comes<br/>Careful verification"]
    C --> D["Handover<br/>Family + ready-to-process claims"]
```

## Who it is for

| Who | What they receive |
| --- | --- |
| **The customer** (for example, Vikram), whether they download it themselves or find it through their bank | A gentle, private way to put their affairs in order, by voice, in their own language |
| **The family** (for example, Priya) | Answers in their loved one's own words, a secure vault, a claims checklist and draft letters |
| **Banks and insurers** | More nominees on file, fewer unclaimed balances, faster claims, less fraud, and a relationship with the next generation |
| **Partners** (lawyers, will writers, safekeeping services) | Customers who need them, at the moment they need them, with a clear summary |
| **The Anvaya review team** | Every document pre-checked, so reviewers spend their time on decisions |

## How it works, in four chapters

### 1. While you are here: talk with Saathi

- **Tap the microphone and just talk.** It feels like a phone call. Saathi answers out loud, in your language, and you can interrupt at any time.
- **Or type, or share a photo or document.** Snap a photo of an insurance policy and Saathi reads it for you.
- **Saathi suggests what to keep.** Small cards appear: a story, a piece of advice, a bank account, a family member. You tap **Keep** or **Not now**.
- **Keep your real voice.** For any story, you can choose to keep the recording of you telling it, so your family can one day hear it from you. Each recording is kept only if you say so.
- **Voice letters.** Record a message for a particular person or moment, such as "for Priya's wedding" or "for Ajay when he turns 18". It stays sealed until the handover, and then reaches only the person you chose.
- **Next steps.** Saathi notices what your family would still be missing, such as "no nominee on this policy" or "no will for the flat", and offers simple next steps, each with a button.
- **A secure vault.** Passwords and account details are locked on your own phone. Nobody else can read them: not Anvaya, not your bank, not Saathi.
- **You choose who receives the vault**, usually a son, daughter or spouse. They accept in their own app, and you confirm a matching code.

### 2. Close the gaps: the partner marketplace

- Partners appear **only when a next step needs one**. "No will for the flat" shows will-writing partners; "originals in a cupboard" shows safekeeping options.
- You **compare and choose**. Saathi explains what each partner does and what it costs, but never recommends one over another.
- With your **consent**, Saathi sends the partner a short, pre-filled summary, so they don't start from zero. The vault is never shared.
- Your own bank or insurer appears first for its own products. Nominee updates go through **the institution's own journey**: Saathi takes you there, but never changes the institution's records itself.

### 3. When the time comes: careful verification

- A family member uploads the **official certificate** from the registrar.
- Automated checks look for signs of forgery, the wrong person, or the wrong kind of document, and explain what they found. If it isn't a genuine certificate, the family is told so.
- **Two different people** on the review team decide: one confirms the loss, the other confirms the family member's legal authority. Overriding the automated checks needs a written reason, and every step is logged.
- A **waiting period** follows. If there has been a mistake and the person is well, one tap on their phone cancels everything.

### 4. The handover: for the family and the institution

- The person you chose **unlocks the vault on their own phone**.
- Your family can **ask Saathi questions**, such as "What insurance did Papa have?". Saathi answers only from what you actually recorded, shows where each answer came from, and says so honestly when something was never recorded.
- When a story was kept with your voice, Saathi offers to play it: *"Would you like to hear him tell it?"* What plays is **your real recording**, never an imitation.
- Each **voice letter** reaches the person it was meant for.
- Saathi prepares a **checklist and draft claim letters** for each bank and insurer.
- A participating bank or insurer receives a **complete, pre-checked claim file**: the verified documents, the review record and the nominee details for its own products. Anvaya doesn't certify legal heirship; the institution's own staff verify and make the final decision.
- Partners help the family with what comes next, such as a succession certificate or transferring property.

## The partner marketplace

| Kind of partner | What they help with |
| --- | --- |
| Legal | Writing a will, registering it, succession certificates, probate |
| Safekeeping | Storing original papers, safe deposit lockers, digital document lockers |
| The customer's bank or insurer | Updating nominees, consolidating old accounts, claims |
| Claims help | Handling claims across many institutions for the family |
| Tax and accounts | The final tax return, closing accounts |
| Property | Transferring land and housing records |
| Licensed advisers | Financial planning, only from licensed partners |

**The promises every partner makes**

- **Neutral.** No partner can pay to be shown first or to be recommended by Saathi. The one exception is the institution offering Anvaya, whose own products come first and are clearly labelled as its own.
- **Clear prices.** Fees are shown before you choose.
- **Vetted.** Only licensed or checked providers are listed.
- **Consent first.** A partner hears about you only after you agree, and receives only the summary they need.
- **No access to the vault**, ever.

**Families across borders.** Many families hold assets in more than one country, or more than one state: an engineer in Singapore with a flat in Karnataka and investments in Mumbai. Saathi gives no legal advice, so it doesn't decide which country's law applies. What it does:
- records **where each asset is**: the country and, in India, the state, since property transfer rules differ by state;
- flags the complexity plainly: *"You have assets in two countries; your family may need a lawyer in each"*;
- shows **partners licensed in each jurisdiction**, and cross-border estate specialists where available;
- groups the family's **checklist and letters by country**, so they can see what each place needs.

Which law applies, and in what order, stays with licensed lawyers.

The marketplace doesn't replace Anvaya's own vault. The vault keeps secrets such as passwords; partners look after physical originals and legal documents.

## Why banks and insurers will want to offer it

| What the institution wants | How Anvaya delivers it |
| --- | --- |
| More complete nominations | Saathi notices missing or outdated nominees and takes the customer into the institution's own nominee journey |
| Faster settlement | Complete, pre-checked files, so the settlement clock (15 days in India) starts sooner and finishes on time |
| Fewer rejected or reopened claims | Documents and authority are checked before the file reaches the institution |
| Fewer unclaimed balances | Every account the customer mentions lands on the family's checklist |
| Less fraud | Forgery checks, a two-person rule, a waiting period, and an owner who can cancel |
| Keeping the next generation | The heir's first real experience of the institution is a kind and helpful one |
| Engaged customers | A warm, genuinely useful reason to open the app, in the customer's own language |

Institutions can offer Anvaya as a branded app, embed it inside their own app, and receive claim files straight into their claims system, without touching their core systems. We propose a six-month pilot with measured outcomes; see [pitch-institutions.md](pitch-institutions.md#the-pilot-we-propose).

## Two ways in, one Anvaya

People can reach Anvaya in two ways. It is the same service, the same Saathi and the same vault either way.

| | Directly | Through a bank or insurer |
| --- | --- | --- |
| How they find it | App store, word of mouth, or a partner such as a will writer | Inside the institution's app, or under its brand |
| Who pays | The customer, with a yearly plan | The institution, as a licence |
| Institutions covered | All of them: Saathi records accounts at any bank or insurer | All of them, with the offering institution's own products shown first |
| Claims | Checklist and draft letters for every institution | The same, plus a ready-to-process claim packet for the offering institution |

Each way strengthens the other. People who come directly show institutions that customers want this; institutions bring customers in large numbers. An account recorded through the direct app is just as useful to the family, and becomes a pre-checked claim the day that institution joins.

## Getting people to start

Nobody opens a succession app on a quiet Tuesday. So Anvaya meets people at the moments when the question already feels natural, and frames it as looking after the family, never as the end:

| Moment | Who sees it | Natural prompt |
| --- | --- | --- |
| Buying or renewing a life policy | Insurer | "Who should receive this, and do they know it exists?" |
| An empty or outdated nominee field | Bank | "Your account has no nominee. It takes two minutes with Saathi." |
| New nomination rules, such as India's four-nominee change (November 2025) | Bank | A reason to reach every existing customer, not only new ones |
| Opening an account, a fixed deposit maturing, a home loan | Bank | A short step inside a journey the customer is already on |
| Marriage, a new child or grandchild, turning 50 or 60, retirement | Either | "Your family has grown. Let's make sure they're looked after." |
| Tax-filing season | Either | "While your papers are out, let's note where everything is." |

Banks and insurers see most of these moments first, which is why offering Anvaya through them matters so much. People who come directly are reached through the same moments: partners such as will writers, word of mouth after a family's handover, and reminders they choose themselves. Prompts and notifications are not yet built; the pilot will measure which moments work best.

## How Anvaya earns

Four revenue streams, each paid by the people who benefit most. A grieving family never has to pay for the handover itself.

| Stream | Who pays | How |
| --- | --- | --- |
| **Plans** | Customers who come directly | A yearly plan, for one person or the whole family. The first conversations are on us, so people feel the value before they choose |
| **Licences** | Banks and insurers | A licence fee to offer Anvaya to their customers, inside their app or under their brand |
| **Marketplace** | Partners | A flat fee to be listed, or per introduction where the rules allow. There is no commission where professional rules forbid it, such as legal advice |
| **Family extras** | Families, only if they want more | The handover comes with a generous included allowance; beyond it, families can buy more questions, letters or time |

**Families never pay for the handover**, however their loved one came to Anvaya, and it comes with a generous allowance of questions, checklists and letters. Asking a grieving family to pay to unlock what their parent left them would break the trust everything here depends on.

For costs, margins and the assumptions behind them, see [business-model.md](business-model.md).

**Why the model compounds**

- **Recurring by nature.** Putting your affairs in order is never finished: new accounts, new policies, new grandchildren. Plans and licences renew year after year.
- **Institutions bring customers at scale.** One licence can reach millions of customers without the cost of winning them one at a time.
- **Every handover introduces the next generation.** The son or daughter who receives a vault has just seen exactly why Anvaya matters, and is the most natural next customer.
- **The marketplace grows with every gap.** Each missing will or nominee Saathi finds is a real need, met by a partner, at the moment it matters.
- **Trust is the moat.** A vault locked on the phone, two-person verification and a neutral marketplace are slow to build and hard to copy.

## Vikram's story

> **Vikram, 58, Bengaluru**, opens Anvaya one evening (he might have downloaded it himself, or found it in his bank's app) and taps the microphone.
> *"My first job was at a textile mill in Mysore in 1987. I gave my whole first salary to my mother."*
> Saathi replies warmly and asks how his mother reacted. A card appears: **Story: First job and salary → Keep**.
>
> Later he mentions: *"I have an LIC policy ending in 4492. Please make Ajay the nominee."*
> Saathi adds the policy, asks *"Who is Ajay?"* ("my nephew"), and reminds him the change must also be submitted to LIC. It appears in his **Next steps** with the exact steps.
>
> He also says the flat in Mysore should go to his wife, Meena. Saathi notes there is no will, and **Next steps** shows three will-writing partners with their prices. Vikram picks one and agrees to share a summary.
>
> Years later, Vikram leaves us, suddenly, as he always feared he might. His daughter **Priya** is not ready; nobody ever is. But she is not lost. She uploads the certificate, the checks find nothing suspicious, and two reviewers confirm. She asks *"What insurance did Papa have?"* and gets the LIC policy, the nominee and a draft claim letter, all in her father's own words. His bank, which offers Anvaya, receives a claim packet that is already checked; for every other institution, Priya has a checklist and a draft letter.
>
> Then she asks about his first job. Saathi tells her about the textile mill and asks, *"Would you like to hear him tell it?"* And there is Vikram, in his own voice, recorded one evening years before, talking about the salary he gave to his mother.
>
> He had left one more thing: a voice letter, *"For Priya, on her wedding day"*.

## What makes Anvaya different

| Most services | Anvaya |
| --- | --- |
| Long forms to fill in | A natural conversation, by voice or text, in your own language |
| Memorial chatbots that imitate the person | **Never** clones a voice or invents answers; only the person's own words, and their **real recorded voice** when they chose to keep it |
| Built afterwards, without the person's consent | Built **by the person, while they are here**, with their approval for every item |
| The company can read your data | The vault is locked on your phone; not the company, not the bank, not Saathi can open it |
| One person can unlock everything | Forgery checks, two reviewers, a waiting period, and the owner can cancel |
| Separate services the family must find alone | Vetted partners, offered only when a real gap needs one |
| Claims start from scratch at every institution | Claims arrive pre-checked, with a full review record |

## Our promises

- **You decide.** Nothing is saved until you tap Keep, and nothing is shared with a partner until you agree.
- **No made-up answers.** If you never said it, Saathi says so.
- **No legal or financial advice from Saathi.** Licensed partners provide it.
- **No paid influence.** Partners can't buy a better place or a recommendation.
- **Your real voice, never a copy.** Your family can hear the recordings you chose to keep and the voice letters you left. Anvaya never clones a voice or builds an avatar.
- **Your secrets stay locked** on your phone until the right person, and only that person, receives them after careful checks.
- **Other people's privacy is respected.** Family members can ask to be added. While you are here, only you can let them in.

## Security, privacy and continuity

| Data | Where it lives | Who can read it |
| --- | --- | --- |
| Passwords and secrets (the vault) | Encrypted on the customer's phone | Only the customer, and after a verified handover, the one person they chose |
| Conversation records: accounts, nominees, family, wishes | Encrypted in the cloud, in-country where required (India regions for Indian institutions) | The customer; Anvaya's service, to answer and prepare the handover; after a verified handover, the chosen family member |
| Voice recordings the customer chose to keep | Encrypted cloud storage | Released only to the verified recipient |
| Claim files | Sent to each participating institution | That institution's claims team, for its own products only |

- **Institutions see only their own products.** Accounts held elsewhere go on the family's checklist, never to another institution, unless the customer agrees.
- **Built for regulated partners.** In India, Anvaya is designed to be engaged under the RBI's rules on outsourcing technology services, with audit rights, and to meet the DPDP Act's consent requirements.
- **Honest about where we are.** Anvaya is not yet certified. An independent penetration test comes before any live customer data, and ISO 27001 and SOC 2 are on the roadmap.
- **Built to outlast us.** A succession service must outlive its vendor: the software and release keys are to be held in escrow, records can be exported at any time in an open format, and institutions get exit support.

## What Saathi does, and what stays with people

Saathi is not a set of screens. It is a companion with clear limits on what it may do by itself:

| What Saathi does | Who decides |
| --- | --- |
| Listens, transcribes, reads documents, suggests cards | Saathi, within fixed rules |
| Finds gaps and matches them to kinds of partners | Saathi suggests; **you choose** |
| Anything that gets saved or shared | Saathi suggests; **you decide** |
| Checks certificates and legal proof for forgery | The checks assist; **a person decides** |
| Answers the family's questions and drafts claims | Only from what was recorded, always with sources |

## Where we are today

The prototype already covers the conversation, next steps, the vault, the two-reviewer verification and waiting period, and the family's questions and letters. The marketplace is shown with one mocked legal partner.

**Not yet built:** prompts at trigger moments and notifications, real recordings and voice letters, the claims connection and claim-file format, single sign-on and in-app embedding, per-role permissions, India data residency, and the full partner catalogue. See [release-notes.md](release-notes.md).


## Sources

[^rbi-claims]: Reserve Bank of India, [*Settlement of Claims Directions, 2025*](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12901&Mode=0) (RBI/2025-26/82, 26 September 2025), paragraphs 5, 31 to 34.
[^irdai]: IRDAI, [*Master Circular on Protection of Policyholders' Interests, 2024*](https://irdai.gov.in/document-detail?documentId=5625747) (5 September 2024).
[^nominees]: Ministry of Finance, [*Key provisions relating to nomination under the Banking Laws (Amendment) Act, 2025*](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2181734), Press Information Bureau, 23 October 2025.
[^dpdp]: Ministry of Electronics and Information Technology, [*DPDP Rules, 2025 notified*](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf), November 2025.
[^hk]: Hong Kong Monetary Authority, [circular of 14 April 2025](https://brdr.hkma.gov.hk/chi/doc-ldg/docId/getPdf/20250414-1-EN/20250414-1-EN.pdf).
[^kr-trust]: Financial Services Commission, Korea, [press release on insurance claim trusts](https://www.fsc.go.kr/no010101/83363), effective 12 November 2024.
[^jp-mynumber]: Digital Agency of Japan, [My Number FAQ on the deposit-account system](https://www.digital.go.jp/en/policies/mynumber_faq_09).
[^my-act]: Unclaimed Moneys (Amendment) Act 2024, as summarised by [Skrine](https://www.skrine.com/insights/alerts/july-2024/unclaimed-moneys-amendment-act-2024-comes-into-ope).
[^in-bank]: Written reply by the Minister of State for Finance, Pankaj Chaudhary, in Parliament, as reported in [*Unclaimed bank deposits in RBI's DEA Fund reach ₹72,454 crore*](https://www.freepressjournal.in/business/unclaimed-bank-deposits-in-rbis-dea-fund-reach-72454-crore-government-promotes-udgam-portal-multiple-nominations), Free Press Journal (PTI). Public sector banks ₹60,571 crore, private banks ₹9,608 crore, foreign banks ₹2,275 crore.
[^in-ins]: Ministry of Finance, [*RBI, IRDAI and SEBI intensify measures to help citizens reclaim unclaimed deposits*](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2244475), Press Information Bureau, 24 March 2026 (written reply in the Rajya Sabha).
[^jp]: Supreme Court of Japan figures reported in [*行き場なく国庫に納められた遺産は1292億円*](https://www.bloomberg.com/jp/news/articles/2025-12-08/T6Q1AWKGIFQM00), Bloomberg, 8 December 2025. See also the Japan Research Institute, [*相続人がいないため国庫帰属となる相続財産は約1,300億円*](https://www.jri.co.jp/page.jsp?id=112109).
[^kr]: Financial Services Commission and the insurance industry, reported in [*Korea insurers struggle to reduce unclaimed policy benefits*](https://www.insurancebusinessmag.com/asia/news/breaking-news/korea-insurers-struggle-to-reduce-unclaimed-policy-benefits-581883.aspx), Insurance Business Asia, 9 July 2026.
[^my]: Accountant General Nor Yati Ahmad, reported in [*RM13.3bil in unclaimed money as of April*](https://www.freemalaysiatoday.com/category/nation/2025/05/17/rm13-3bil-in-unclaimed-money-as-of-april), Free Malaysia Today, 17 May 2025.
[^au]: Australian Taxation Office, [*Total lost (fund-held) and ATO-held super*](https://www.ato.gov.au/about-ato/research-and-statistics/in-detail/super-statistics/super-accounts-data/super-data-lost-unclaimed-multiple-accounts-and-consolidations/total-lost-fund-held-and-ato-held-super), and the ATO media release [*$21 billion in lost super*](https://www.ato.gov.au/media-centre/21-billion-dollars-in-lost-super-find-yours).
[^sg]: CPF Board, [*CPF nominations*](https://www.cpf.gov.sg/member/infohub/reports-and-statistics/cpf-trends/cpf-nominations) (2023 data, updated 4 November 2024), and [*Making a CPF nomination*](https://www.cpf.gov.sg/member/account-services/providing-for-your-loved-ones/making-a-cpf-nomination).

---

*Nobody can choose when they leave. Anvaya makes sure the people they love are not left without answers.*
