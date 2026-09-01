# TPV Growth Engine

Engineered organic growth pipeline for Konfío's TPV (card terminal) product: it finds Mexican SMEs that **do not accept card payments**, ranks them by purchase intent, and drafts a personalized WhatsApp opener that quotes the evidence it found.

No paid media. No paid data vendors. The first 500 qualified leads cost **$0** — they fit inside the Google Places and Gemini free tiers.

## The idea in one paragraph

Most lead lists are a census: they tell you a business exists. This one is an event feed. A merchant becomes a lead only when there is observable, quotable evidence *right now* that they need a terminal — Google's structured `paymentOptions` field says cash only, or their own customers wrote "solo aceptan efectivo" in a review. That evidence is not just a filter; it becomes the first line of the outreach message, which is why the reply rate should beat generic cold outbound by an order of magnitude.

## Pipeline

```
Places Text Search  ──▶  Place Details  ──▶  Gemini (structured output)  ──▶  processed_leads.csv
   giro × plaza          paymentOptions,        MCC + FAMILIA
   search plan           reviews, phone         payment signal + evidence
                                                intent score 0-100
                                                eligibility + reason
                                                es-MX WhatsApp message
```

Qualification is enforced by code, not by judgment. A lead is qualified only when all three hold:

1. **ICP fit** — its MCC maps to one of the 10 in-scope FAMILIA groups (140 of 806 catalog codes). ISO 18245 reserves 3000–3999 for airline and hotel brands; those are excluded by construction.
2. **Trigger** — a payment signal ranked `confirmed_no_card` > `inferred_no_card` > `competitor_terminal`. Merchants that already accept cards without pain are dropped.
3. **Reachability** — a phone number in the public record.

Disqualifiers are applied *before* any LLM call is made, so the expensive step only ever sees candidates that already passed the cheap checks.

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

All paths are resolved relative to the repository root, so the pipeline runs from a fresh clone on any machine.

### Credentials

Both keys are free. They are read from the environment only — never from source.

| Variable | Where to get it |
| :-- | :-- |
| `GOOGLE_MAPS_API_KEY` | Google Cloud Console → enable **Places API (New)** → Credentials → API key |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — no credit card required |

The pipeline fails fast with a clear message if either is missing, rather than dying halfway through a metered API run.

## Layout

```
data/mcc_catalog.csv     806 ISO 18245 codes mapped to Konfío's FAMILIA taxonomy
data/raw_leads.json      Places output, before any LLM involvement
data/processed_leads.csv Classified, scored, message-ready leads
src/config.py            Environment, search plan, in-scope familias
src/models.py            Pydantic contracts; LeadClassification doubles as the Gemini response schema
src/mcc.py               Catalog loading, lookup, scope checks
tests/                   pytest
```

## Design notes

**Closed-vocabulary classification.** The in-scope catalog is handed to the model in the prompt and `LeadClassification` is passed as the response schema, so the LLM cannot return an MCC that does not exist. Every returned code is re-validated against the catalog afterwards anyway.

**Cost control by construction.** Field masks are scoped to the SKU tier actually needed, `MAX_PLACE_DETAILS` caps a run so a bug cannot spill past the free tier, and the catalog prompt block is amortized by batching merchants per LLM call instead of resending a 3K-token prefix 500 times.

**Traceability.** Every record carries a ULID `trace_id` and a `schema_version`, so a lead in the CSV can be joined back to the exact API run that produced it.

## Status

Scaffold and data contracts are in place; extraction and enrichment are next. Funnel economics (conversion, implied CAC, payback) are published here once the first full run produces real numbers — the model exists, but reporting projected figures as measured ones would be the wrong kind of confident.
