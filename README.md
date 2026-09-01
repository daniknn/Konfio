# TPV Growth Engine

Engineered organic growth pipeline for Konfío's TPV (card terminal) product: it finds Mexican SMEs that **do not accept card payments**, ranks them by purchase intent, and drafts a personalized WhatsApp opener that quotes the evidence it found.

No paid media, no paid data vendors, no manual steps. One command, from a fresh clone.

## The idea in one paragraph

Most lead lists are a census: they tell you a business exists. This one is an event feed. A merchant becomes a lead only when there is observable, quotable evidence *right now* that they need a terminal — Google's structured `paymentOptions` field says cash only, or their own customers wrote "solo aceptan efectivo" in a review. That evidence is not just a filter; it becomes the first line of the outreach message, which is why the reply rate should beat generic cold outbound by a wide margin.

## What the first run actually taught us

The first full run scanned 600 merchants across `taquería / cafetería / farmacia …` × `Ciudad de México / Guadalajara / Monterrey` and returned **31 qualified leads**. A 5% yield.

The instinct is to loosen the qualifier. The data said otherwise:

- Google's structured field reported that **76% of those 600 already accept cards**. They are not a targeting failure; they are genuinely not the segment.
- The obvious fallback — find card-accepting merchants whose reviews complain about surcharges, minimums or a terminal that never works, and sell displacement — collapsed under measurement. Across those 458 merchants, **6 of them (1%)** had any such complaint in their reviews. Google returns ~4.7 reviews per merchant and skews them positive.

So the qualifier was right and the **sample** was wrong. Text Search ranks by prominence. Pointing it at a metro area returns the most established businesses in the most banked districts of the country, which is a near-perfect filter for merchants that already own a terminal. A 100-call probe confirmed it:

| Query | Cash-only |
| :-- | --: |
| `cafetería en Polanco, Ciudad de México` *(control)* | **0%** (0/20) |
| Original plan — 17 giros × 3 metro cores | **5%** (29/600) |
| `fonda en Nezahualcóyotl` | **40%** |
| `papelería en Chalco` | **45%** |
| `recaudería en Ecatepec` | **50%** |
| `tortillería en Iztapalapa` | **80%** (16/20) |

Same code, same cost per merchant, ~10x the addressable rate. The scraper was never the hard part; the targeting hypothesis is the product.

## What the second run taught us

Re-aimed at 20 peripheral municipalities × 18 cash-operated giros, the pipeline screened 2,500 merchants and returned **226 qualified leads** — 9% overall, and the run produced enough volume to rank its own inputs. Three cuts followed, each from measurement rather than intuition:

- **Giros were ranked by qualified leads per screening call, not by how cash-heavy the trade feels.** The two rankings disagree: recaudería is 39% cash-only but converts at 9.4%, because those owners rarely publish a phone. The bottom nine giros spent 1,244 screening calls and returned 13 leads between them. The plan now runs the **top five** — fonda económica 29.8%, tortillería 27.1%, taquería 18.9%, panadería 18.1%, miscelánea 14.1%.
- **Merchants whose `paymentOptions` field is silent were dropped.** They cost one Atmosphere call each — 331 of them — and converted at 1.5%. A silent field usually means a quiet listing, not a cash register. Removing that cohort eliminated *all* mandatory reviews spend.
- **Reviews buy message quality, not qualification.** For 222 of the 226 eligible leads the evidence quoted in the message came from Google's structured field, not from a review. So reviews became purely optional, capped by `REVIEW_BUDGET`, and the first thing cut when the allowance runs out.

The dominant remaining loss is reachability: only **46% of cash-only merchants publish a phone number**. That, not the qualifier, is what the next iteration has to attack.

## The delivered list

The third run applied those three cuts and added **298 leads on 1,400 screenings** without re-screening a single merchant already on disk. Committed in `data/`:

| | |
| :-- | --: |
| Qualified leads | **524** |
| Merchants screened, cumulative | 3,900 |
| With a phone number | 524 / 524 |
| Payment signal `confirmed_no_card` | 520 (99.2%) |
| Average intent score | 77.8 |
| Average message length | 38 words (max 47) |
| Compliance violations | **0** |

Prospect → qualified went from 40% to **100%**: with the silent-field cohort gone, every merchant that survives the screen also survives the model. The cost of a qualified lead is now just the cost of finding one — **4.7 Enterprise screenings**.

## Funnel economics

### What a lead costs

Measured from the third run — the shipped configuration — and normalized to 500 leads. Priced at Google's **list price**, not at what a trial credit happens to absorb:

| Line | Volume | Rate | Cost |
| :-- | --: | --: | --: |
| Place Details — Enterprise screen | 2,349 | $20 / 1,000 | $46.98 |
| Text Search (Pro) | 320 | $32 / 1,000 | $10.26 |
| Place Details — Atmosphere reviews | 218 | $25 / 1,000 | $5.45 |
| Gemini Flash in / out | 375K / 97K tokens | $0.30 / $2.50 per 1M | $0.36 |
| **Total** | | | **$63.04 USD** |
| **Per qualified lead** | | | **$0.126 USD ≈ $2.33 MXN** |

Run it as a monthly job and the free allowances absorb Text Search and reviews entirely, leaving 1,349 billable screenings: **~$27 USD/month for 500 leads**. Both numbers are stated because only the first one is a real unit cost — the second is a subsidy, and a channel justified on a subsidy stops working the month it grows.

Worth naming: **Gemini is 0.6% of the bill.** The expensive part of an LLM pipeline here is not the LLM, it is the paid data underneath it. That is why the engineering went into the Places call pattern and not into prompt golf.

### Conversion

Measured through qualification; **modeled after it** — nothing has been sent yet, and labeling a projection as a result is the fastest way to lose the argument.

| Stage | Rate | Count | Basis |
| :-- | --: | --: | :-- |
| Merchants screened | — | 2,349 | measured |
| Qualified leads | 21.3% | **500** | measured |
| Reachable on WhatsApp | 85% | 425 | assumption |
| Replies | 11% | 46 | assumption — evidence-led opener vs. 2-3% generic cold |
| Demos | 35% of replies | 16 | assumption |
| **Terminals activated** | 40% of demos | **6** | assumption |

Lead → activated terminal: **1.2%**.

### CAC, LTV, payback

| | MXN |
| :-- | --: |
| Technology CAC per terminal | **$194** |
| One closer, fully loaded, ~425 conversations/month | $25,000 |
| **Fully-loaded CAC per terminal** | **$4,361** |
| Year-1 TPV revenue per merchant | $15,000 |
| Credit attach — 20% at $35,000 year-1 revenue | $7,000 |
| **Blended year-1 value** | **$22,000** |
| **Payback** | **2.4 months** |
| LTV:CAC at 24-month retention | **~10:1** |

**The honest headline is $4,361, not $194.** The $194 is the marginal technology cost, and quoting it as "the CAC" is precisely the vanity-metric trap: the pipeline generates leads, it does not close them. But the *shape* of that number is the actual finding — **technology is 4.5% of CAC and human effort is 95%** — which says exactly where the next automation should go, and it is not the scraper.

The second stage is the real argument. A terminal is a $15,000 MXN/year product; a terminal that converts a fifth of its base into credit customers is worth $22,000. Every peso of card volume crossing that terminal is underwriting data Konfío owns, so the merchant who could not be underwritten in month 0 is underwritable in month 6 **because of the terminal**. Konfío stops looking for creditworthy SMEs and starts manufacturing them.

### Where this breaks

- **Reply rate is the load-bearing assumption.** Below ~6%, fully-loaded CAC roughly doubles and payback slips past five months. It is also the cheapest thing to measure: 100 sends settles it.
- **A plaza depletes, and the signal is already visible.** Cost per lead fell hard — 11.1 screenings each in run 2, 4.7 in run 3 — but that is the config cuts, not the territory. Underneath them the prospect rate *slipped*, 22.5% → 21.3%, even though run 3 ran only the five best giros and should therefore have scored higher. The best merchants in these 20 municipalities are being skimmed. Growth needs new zonas, which is why `ZONAS` is a config list and not a constant.
- **Phone coverage is the ceiling.** 54% of confirmed cash-only merchants publish no phone. That is the largest single loss in the funnel and no amount of prompt work recovers it — it needs a second contact channel.

## Pipeline

```
Text Search          Place Details          Place Details        Gemini              processed_leads.csv
giro × zona     ──▶  SCREEN (Enterprise) ──▶ REVIEWS (Atmos.) ──▶ structured output ─▶
5 × 20 = 100         paymentOptions          optional, only for   MCC + FAMILIA
queries              phone, ratings          merchants worth      payment signal + evidence
                                             quoting              intent score 0-100
                                                                  eligibility + reason
                                                                  es-MX WhatsApp message
```

Qualification is enforced by code, not by judgment. A lead is qualified only when all three hold:

1. **ICP fit** — its MCC maps to one of the 10 in-scope FAMILIA groups (140 of 806 catalog codes). ISO 18245 reserves 3000–3999 for airline and hotel brands; those are excluded by construction.
2. **Trigger** — a payment signal ranked `confirmed_no_card` > `inferred_no_card` > `competitor_terminal`. Merchants that already accept cards without pain are dropped.
3. **Reachability** — a phone number in the public record.

Every disqualifier that code can decide is applied *before* the next call is paid for, so each stage only ever sees candidates the cheaper stage could not rule out.

**Nothing the model returns is trusted on its own.** The catalog overwrites the FAMILIA it invents, Google's structured `paymentOptions` overrides the payment signal it guesses, and eligibility is decided in code afterwards. The model's real job is the two things code cannot do: read reviews and write Spanish.

## Spending in the shape of the price list

Google bills Place Details per call at the highest field tier touched, and each tier has its own free monthly allowance:

| SKU | Price / 1,000 | Free / month |
| :-- | --: | --: |
| Text Search (Pro) | $32 | 5,000 |
| Place Details (Enterprise) — `paymentOptions`, phone | $20 | 1,000 |
| Place Details (Enterprise + Atmosphere) — `reviews` | $25 | 1,000 |

Asking for `paymentOptions` and `reviews` in one call — the obvious implementation — charges every merchant at the top tier and burns one shared allowance. But `paymentOptions` alone disqualifies most of them, and it is a whole SKU cheaper.

So the call is split. Every candidate gets an **Enterprise screen**; only merchants that survive it earn an **Atmosphere reviews call**. And since the structured field is what qualifies a merchant, that second call is never load-bearing — it buys a sharper first line, nothing more. It is therefore rationed twice: only for merchants with at least 30 reviews (below that, Google returns three and none of them mention how the customer paid), and only while `REVIEW_BUDGET` lasts.

The run also stops on the result rather than the budget: screening ends as soon as `TARGET_LEADS × 1.10` prospects have survived it. `MAX_PLACE_DETAILS` is a safety net against a runaway loop, not the intended stopping condition.

**Runs accumulate.** A merchant already in `data/` is never re-screened: the pipeline reads what is on disk, extracts only the shortfall to `TARGET_LEADS`, and merges by `place_id`. This is a standing weekly job, so the second week should cost what the *new* merchants cost, not what the whole list costs. `uv run tpv-pipeline --fresh` starts the list over.

## Quickstart

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/daniknn/Konfio.git
cd Konfio
uv venv --python 3.12
uv pip install -e ".[dev]"
cp .env.example .env      # fill in your two API keys
uv run tpv-pipeline
```

All paths resolve relative to the repository root, so the pipeline runs from a fresh clone on any machine.

### Credentials

Read from the environment only — never from source, never committed.

| Variable | Where to get it |
| :-- | :-- |
| `GOOGLE_MAPS_API_KEY` | Google Cloud Console → enable **Places API (New)** → Credentials → API key |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

The pipeline fails fast with a clear message if either is missing, rather than dying halfway through a metered API run.

## Layout

```
data/mcc_catalog.csv     806 ISO 18245 codes mapped to Konfío's FAMILIA taxonomy
data/raw_leads.json      Places output, before any LLM involvement
data/processed_leads.csv Classified, scored, message-ready leads
src/config.py            Environment, search plan, in-scope familias, compliance terms
src/models.py            Pydantic contracts; ClassifiedLead doubles as the Gemini response schema
src/mcc.py               Catalog loading, lookup, scope checks
src/places.py            Places API client: search, Enterprise screen, Atmosphere reviews
src/extract.py           Phase 1 — search plan, via-negativa filtering, budget guards
src/enrich.py            Phase 2 — Gemini classification, reconciliation, CSV output
src/pipeline.py          Entry point (`tpv-pipeline`)
tests/                   pytest — 57 tests, no network
```

## Design notes

**Closed-vocabulary classification.** The in-scope catalog is handed to the model in the prompt and `list[ClassifiedLead]` is passed as the response schema, so the LLM cannot return an MCC that does not exist. Every returned code is re-validated against the catalog anyway.

**Replies are matched by echoed `place_id`, never by array position.** A model that returns 9 items for a batch of 10 would otherwise silently shift every downstream merchant's classification onto the wrong business. Unknown IDs are dropped and logged.

**Compliance is a hard stop, length is not.** Konfío is a SOFOM E.N.R. with a pending CNBV banking licence, so outbound copy that implies deposit-taking creates regulatory exposure. A message containing any prohibited term disqualifies the lead outright rather than being quietly edited. An over-long message only warns — that is a style defect, not a legal one.

**Failures are contained to their blast radius.** A dead search query does not abort a run that already paid for the others; a failed batch does not discard the batches around it; a failed reviews call keeps the merchant, without its reviews.

**Traceability.** Every record carries a ULID `trace_id` and a `schema_version`, so a lead in the CSV joins back to the exact API run that produced it.

**Data retention.** `data/raw_leads.json` is a point-in-time snapshot. Google Maps Platform terms allow caching Places content for up to 30 days (`place_id` is exempt), so the committed file is a reproducibility artifact for this submission, not a database to build on. The pipeline re-fetches on every run.
