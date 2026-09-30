# Anvaya: Business model and unit economics

*Team Saathi · Revision 1, 30 September 2026 · the idea is in [concept.md](concept.md); how it is built is in [architecture.md](architecture.md)*

*All figures here are **illustrative**: prices are proposals, and costs are estimates from published list prices and our own assumptions. Each one is listed below so it can be replaced with pilot data.*

---

## In short

Anvaya earns from four sources: yearly plans for people who come directly, licences for banks and insurers, fees from partners in the marketplace, and optional extras for families. Families never pay for the handover itself.

The biggest cost of serving someone is the **first year**, when most of the conversation happens. After that, a customer mostly keeps their records up to date. So the margin on each customer **rises every year they stay**.

| Per customer, per year | Year 1 | Year 2 onwards |
| --- | --- | --- |
| **Direct plan, paid through the app stores** | 48% margin | 69% margin |
| **Direct plan, paid on the web** | 60% margin | 81% margin |
| **Bank or insurer licence** | 72% margin | 72% margin |
| **Marketplace introductions** | about 85% margin | about 85% margin |
| **Family extras** | high; priced above the cost of the extra use | high |

These are contribution margins: revenue less the cost of serving that customer. The fixed costs of running the company are covered separately below.

## Illustrative prices

| Stream | Illustrative price | Who pays |
| --- | --- | --- |
| Direct plan | ₹1,499 a year for one person (about US$17) | The customer |
| Family plan | A higher price covering a spouse and parents | The customer |
| Licence | ₹499 a year per enrolled customer (about US$5.70) | The bank or insurer |
| Marketplace | A flat listing fee, or a fee per introduction where the rules allow | The partner |
| Family extras | A small top-up once the family has used its included allowance | The family, only if they want more |

There is no commission where professional rules forbid it, such as for legal advice. The first conversations with Saathi are free to try, so people feel the value before they choose a plan.

## What it costs to serve one customer

### Direct plan (₹1,499 a year)

| Cost | Year 1 | Year 2 onwards | What drives it |
| --- | --- | --- | --- |
| Voice conversations | ₹317 | ₹79 | 120 minutes in the first year, 30 minutes a year after, at ₹2.64 a minute[^voice] |
| Typed chat, reading documents, spoken replies | ₹88 | ₹35 | Low-cost language services; each message costs a fraction of a paisa[^text] |
| Cloud hosting, storage and encryption keys | ₹53 | ₹53 | Servers, database, document and voice-recording storage, and key management on Google Cloud. An hour of compressed voice is only tens of megabytes |
| Handover reviews | ₹13 | ₹13 | About ₹1,320 per case for two reviewers, the forgery checks and family support, assuming 1 in 100 customers needs a handover each year |
| Customer support and partner vetting | ₹88 | ₹53 | More help while people get started |
| Payment and app-store fees | ₹225 | ₹225 | 15% through the app stores[^stores] |
| **Total cost** | **₹784** | **₹458** | |
| **Margin** | **48%** | **69%** | |

Paid on the web, where payment fees are about 3% (₹45) instead of 15%, the total cost falls to ₹604 in year 1 and ₹278 after, giving margins of **60%** and **81%**.

### Licence (₹499 a year per enrolled customer)

Not every enrolled customer is active in a given year. We assume 4 in 10 are, each using about as much as a direct customer in year two.

| Cost per enrolled customer | Per year |
| --- | --- |
| Voice conversations | ₹32 |
| Typed chat, documents, spoken replies | ₹14 |
| Cloud hosting, storage and encryption keys | ₹26 |
| Handover reviews | ₹13 |
| Customer support | ₹26 |
| Integration and account management for the institution | ₹26 |
| **Total cost** | **₹137** |
| **Margin** | **72%** |

There are no app-store fees on a licence, and the institution brings the customers, so there is no cost to win them one by one.

### The family's allowance and extras

When the time comes, the family receives the handover and a **generous included allowance** at no cost: unlocking the vault, a set number of questions to Saathi, and the checklist and draft letters for every institution. The cost of that allowance is part of the handover cost above, already paid for by the plan or licence.

If the family wants more than the allowance, such as more questions, more letters, or keeping the records open for longer, they can buy extras. Extras are priced above the cost of the extra use, so they add margin rather than cost. The family can also move onto a plan of their own, which is how the next generation becomes a customer.

### Marketplace

A partner fee has almost no cost of service: the introduction is a short summary the customer has already approved. The main costs are vetting partners and handling payments, leaving a margin of about **85%**.

## What keeps costs low

- **The most affordable services that do the job.** Chat, extraction and document reading use the lowest-cost tier of language services, with more capable ones only as a fallback.[^text]
- **Voice when people want it.** Voice is the largest variable cost. Typing and photos cost a small fraction of it, and many people mix the two.
- **The vault lives on the phone.** Secrets are encrypted on the customer's device, so there is no large, costly store of secrets to run and protect.
- **People decide; automated checks prepare.** Every certificate is pre-checked before a reviewer sees it, so reviewer time goes on decisions, not paperwork.
- **Conversation tapers off.** Most of the talking happens in year one; after that, Saathi mostly helps keep records up to date.

## Fixed costs of running the company

These don't grow with each customer, so their share of revenue falls as Anvaya grows:

- **Product and engineering**: the app, the service and new languages
- **Security and compliance**: independent security audits, certification (such as ISO 27001), and data-protection compliance (such as India's Digital Personal Data Protection Act)
- **The review team**: a trained, always-available team with two-person cover, audit logging and quality checks
- **Partner network**: vetting, contracts and keeping listings current
- **Institution partnerships**: sales, integration and regulatory reviews with each bank or insurer

## Why the margins grow over time

- **Recurring revenue, falling cost.** Plans and licences renew each year, while the cost of serving each customer falls after year one.
- **Institutions bring customers at scale.** A single licence can reach millions of customers without paying to win each one.
- **Every handover introduces the next generation.** The son or daughter who receives a vault is the most natural next customer, and costs almost nothing to acquire.
- **The marketplace grows with every gap.** Each missing will or nominee is a real need, met by a partner, at close to pure margin.
- **Room to cut costs further.** Caching the conversation context, batch processing for work that isn't urgent, and newer lower-cost service tiers can all bring the cost per customer down as volumes grow.

## Assumptions to validate in the pilot

| Assumption | Used above | How we'll measure it |
| --- | --- | --- |
| Voice minutes per customer | 120 in year 1, 30 after | Usage logs |
| All-in cost of a voice minute | ₹2.64 (US$0.03), above the list price to allow for context and extraction[^voice] | Cloud billing |
| Share of licensed customers active each year | 4 in 10 | Usage logs |
| Customers needing a handover each year | 1 in 100 | Case volumes |
| Share of families who buy extras beyond the allowance | Not counted above (upside) | Purchase data |
| Reviewer and support time per handover | About ₹1,320 per case | Time tracking |
| Payment channel | App stores at 15%, or web at 3% | Payment mix |
| Exchange rate | ₹88 to the US dollar | Update at each revision |

---

[^voice]: Google's list price for its live voice service is about US$0.005 per minute of audio in and US$0.018 per minute of audio out ([Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing), updated 24 September 2026). We use US$0.03 a minute to allow for conversation context and for turning each exchange into cards.
[^text]: The lowest-cost language tier is listed at US$0.25 per million input tokens and US$1.50 per million output tokens ([Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)). Spoken replies cost about US$0.0015 per 10 seconds of audio until December 2026, and US$0.003 from January 2027.
[^stores]: Apple and Google both charge 15% on subscriptions under their standard subscription or small-business terms, and up to 30% otherwise.
