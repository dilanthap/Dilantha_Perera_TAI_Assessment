# AI Policy & Risk Simulator

**A new kind of compliance training, built on TAI Labs' existing core competency.**

Tai already does the hard part: retrieval-augmented tutoring over a company's own
source material, grounded and cited back to source. This prototype points that
exact mechanic at a different, highly sellable use case. A client uploads their
own AI-usage policy; the tool generates realistic workplace scenarios grounded in
*that specific document*, has employees answer in free text, and then scores the
**quality of their reasoning** against the policy — not just whether they picked
the right answer — citing the exact section relied on. The target buyer is the
regulated client (finance, healthcare, legal) who needs training records that are
defensible to a regulator, which is precisely the customer for whom "grounded and
cited" is a purchasing requirement rather than a nice-to-have.

---

## Quick start

```bash
git clone https://github.com/dilanthap/Dilantha_Perera_TAI_Assessment.git && cd Dilantha_Perera_TAI_Assessment

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then add your key(s) to .env
# ANTHROPIC_API_KEY=sk-ant-...
# VOYAGE_API_KEY=pa-...             # optional — only needed for the policy library, below

uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**, click **Load sample policy**, and run the loop.

There are two independent tools in this app, at two different scales — see
[The two retrieval strategies](#the-two-retrieval-strategies) for why they're
built differently rather than sharing one approach:

- **`/`** — upload one policy, get scenarios generated from its full text.
- **`/library`** — upload several documents, ask ad-hoc questions answered from
  retrieved excerpts. Needs `VOYAGE_API_KEY` for real embeddings; runs on
  fixtures under `DEMO_MODE=1` like everything else.

Both accept `.txt`, `.md`, or `.pdf` uploads (PDF is best-effort via `pypdf`),
and both let you remove what you've added — a policy and its scenarios/answers
from `/`, a document and its chunks from `/library` — with a confirmation
before anything is deleted.

### Try it without an API key

```bash
DEMO_MODE=1 uvicorn app.main:app --reload
```

Demo mode serves bundled sample data instead of calling the API — no key, no
network. **The scenarios, scores, and library answers are fixed**; they don't
read what you actually upload or type, they just cycle through a few canned
examples (see `app/fixtures.py`). The UI, routing, and database loop are all
real — only the model calls are swapped out. It exists so you can see the app
run before spending a key on it, and so the UI could be tested independently
of model behaviour during development.

A ready-made sample policy is in [`samples/northwind_ai_policy.md`](samples/northwind_ai_policy.md)
— a realistic one-page AI-usage policy for a fictional financial firm.

---

## Deploying to Vercel

Two things change between "runs on my laptop" and "runs as a public URL",
and skipping either one produces a real problem, not a cosmetic one:

1. **Storage.** [`app/database.py`](app/database.py) uses a local SQLite
   *file* by default. That's the right call for local dev — zero setup — but
   Vercel serverless functions have no persistent disk between invocations.
   Deployed as-is, an upload can succeed and then 404 on the very next
   request, unpredictably, depending on which instance happens to serve it.
2. **Cost.** `/upload`, `/policy/{id}/answer`, `/library/documents`, and
   `/library/ask` each make a real, billed call to Anthropic or Voyage. With
   no rate limit, a public URL with no auth in front of it means anyone —
   or any bot, or one broken retry loop — can run up real charges with no
   ceiling.

Both are handled, and both degrade gracefully when unconfigured — locally,
neither of the services below is needed, and the app runs exactly as it did
before this section existed.

### 1. Add a Postgres database (Neon)

Vercel Postgres was retired in December 2024; Neon is the current path via
the Vercel Marketplace.

1. In your Vercel project → **Storage** → **Marketplace Database Providers**
   → **Neon** → create a database.
2. This sets `DATABASE_URL` on your project automatically.
3. That's it — [`app/database.py`](app/database.py) checks for `DATABASE_URL`
   at startup: set, it connects to Postgres (via `psycopg`, pinned in
   `requirements.txt`); unset, it falls back to the local SQLite file exactly
   as before. `Base.metadata.create_all()` runs on every cold start and is
   idempotent, so the tables get created on first deploy with no separate
   migration step.

### 2. Add rate limiting (Upstash Redis)

An in-memory counter doesn't work here — Vercel functions are stateless
between invocations, so it resets on every cold start and wouldn't actually
stop sustained abuse. [`app/ratelimit.py`](app/ratelimit.py) uses Upstash
Redis instead, for a counter that's shared across instances.

1. In your Vercel project → **Storage** → **Marketplace Database Providers**
   → **Upstash** → create a Redis database.
2. This sets `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`
   automatically.
3. `app/ratelimit.py` checks for both at import time: set, every route that
   calls a paid API is limited to 10 requests/minute per visitor (tune
   `_LIMIT_MAX_REQUESTS` / `_LIMIT_WINDOW_SECONDS` — a starting guess, not a
   measured value); unset, those routes run with no rate limiting, the same
   as before this file existed.

### 3. Set a hard spend cap, regardless of the above

Rate limiting reduces exposure; it isn't a guarantee — a determined abuser
distributing requests across enough IPs, or a limit set too loosely, can
still get through. The one thing that actually puts a ceiling on the bill is
**setting a spend limit directly with the API providers**: the Anthropic
Console and the Voyage AI dashboard both support this. Do this regardless of
whether rate limiting is configured — it's the backstop that holds even if
everything else fails.

### 4. Add your API keys and deploy

`ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` (see Quick start, above) still need
setting as environment variables on the Vercel project — the Marketplace
integrations above don't provide these. Then connect the GitHub repo to a
new Vercel project (or `vercel deploy`); no other configuration is needed —
[`vercel.json`](vercel.json) sets a 60-second function timeout (LLM and
embedding calls are the slow steps), and Vercel auto-detects the FastAPI
`app` instance at `app/main.py` with zero entrypoint configuration.

---

## The loop

```
Upload policy  ──▶  Generate scenarios  ──▶  Answer in free text  ──▶  Score reasoning  ──▶  Results
   (Policy)          (Scenario × 3-5)             (Response)            (0-100 + citation)
```

| File | Role |
|---|---|
| `app/main.py` | FastAPI routes for the quiz loop; catches every `LLMError` and renders a clean error page |
| `app/llm.py` | Anthropic client, timeouts, error translation, JSON extraction, demo mode |
| `app/prompts.py` | Every prompt, isolated for reading and tuning |
| `app/generation.py` | Policy → grounded scenarios |
| `app/scoring.py` | Scenario + excerpt + answer → score, verdict, feedback, citation |
| `app/models.py` | `Policy` → `Scenario` → `Response`, plus `LibraryDocument` → `LibraryChunk` |
| `app/library.py` | FastAPI routes for the policy library (`/library`) |
| `app/rag.py` | Chunking, embedding (Voyage AI), retrieval and answer generation for the library |
| `app/templating.py` | Shared `Jinja2Templates` instance used by both routers, plus the `section_tag` filter and `render_error` |
| `app/text_utils.py` | Shared upload helpers used by both routers: PDF/.txt/.md text extraction, filename-derived titles |
| `app/ratelimit.py` | Rate limiting for the routes that call a paid API — see "Deploying to Vercel" |

The policy library is a second, independent tool at `/library` — chunk → embed
→ retrieve → answer, for when a single policy's full text stops being the right
unit of context. See below.

---

## What makes the scoring different

Conventional compliance training asks *"did they pick the right option?"*. That
tests recall, and it produces a training record proving very little — an employee
can pass by pattern-matching on whichever answer looks strictest.

This tool asks whether the employee can **apply** the policy to a situation nobody
showed them, which is only answerable against free text. The grading contract in
`prompts.py` is explicit that:

- a **right conclusion reached for the wrong reason** scores in the middle band,
  because that employee will get it wrong when the details change;
- a **conclusion asserted with no reasoning** scores poorly regardless of
  correctness, because there is nothing there to audit;
- **over-compliance that cites a rule not in the document is still an error** —
  inventing obligations is a failure mode in both directions.

Every assessment quotes the governing policy text verbatim, so a compliance team
can audit *why* an employee was scored the way they were.

### The anti-fabrication guarantee

For a regulated client, a tool that invents a plausible-sounding rule is worse
than useless: it generates training records asserting obligations the company
never adopted. Both system prompts state that the uploaded policy is the only
source of authority, and that **if the policy is silent, saying so is the correct
answer**. When that happens the citation renders as *"Not addressed by this
policy"* in a visually distinct block rather than a fabricated section.

The sample policy is deliberately silent on one plausible topic — **AI use in
hiring and candidate screening**. To see the guarantee work, write an answer that
reasons about recruitment and watch the model decline to invent a rule for it.

---

## Design decisions

### The two retrieval strategies

The reflex for "answer questions about a document" is chunk → embed → retrieve
top-k. This app uses that reflex in exactly one of its two tools, and
deliberately not the other — same underlying question, different answer at
different scale.

**The quiz (`/`) does NOT use retrieval.** An AI-usage policy is a few pages
(the sample is ~1,100 tokens) and fits in the context window many times over,
so `app/generation.py` sends the whole document on every call. That buys three
things chunked retrieval would cost:

1. **Completeness.** Generation needs the *whole* policy to pick provisions that
   interact and to spread scenarios across different sections. Top-k retrieval
   optimises for local relevance and would systematically miss cross-section
   tensions — exactly the non-obvious cases worth training on.
2. **Citation fidelity.** The returned excerpt is verbatim from the real
   document, not from a chunk boundary that may have severed a clause from the
   heading qualifying it.
3. **Honest silence.** A retrieval miss and a genuine gap in the policy look
   identical to the model. With the full text in context, silence is real silence
   — which is what makes the anti-fabrication guarantee above meaningful.

**The policy library (`/library`) DOES use retrieval**, in `app/rag.py`,
because at that scale the tradeoff flips. A library can hold several documents,
or documents long enough that resending all of them on every question is
neither cheap nor reliable — the problem retrieval actually solves. Rather than
wave that scenario away, it's built out for real:

- **Section-aware chunking.** Splits on `## Heading` boundaries first (falling
  back to numbered-subsection, then paragraph-aligned splitting only when a
  section runs long), so a chunk's heading stays attached to its body — the
  exact citation-fidelity property the quiz gets for free from full context.
- **Voyage AI embeddings** (`voyage-law-2`, tuned for legal/compliance text —
  a closer fit here than a general-purpose model) with a plain cosine-similarity
  scan over the library's chunks. No vector database: at library scale (a
  handful of documents, at most a few dozen chunks) a linear scan in Python is
  fast enough, and it costs zero extra infrastructure — the same reasoning
  `app/database.py` applies to choosing SQLite over Postgres.
- **A retrieval-confidence signal — but the model's own judgment is the
  headline, not the raw similarity number.** The first version showed a
  "high/low/none confidence" pill computed straight from the top chunk's
  cosine similarity, before the model had read anything. Live testing found
  it actively misleading in both directions: a question with no real answer
  in the library scored 0.51 (nominally "high") while a correctly-answered
  question's top chunk scored 0.45 ("low"). The pill now reflects `addressed`
  — the model's own explicit judgment, from actually reading the retrieved
  text, of whether it found an answer — which got both of those cases right
  where the raw score didn't. Per-chunk similarity scores are still shown,
  demoted to supporting detail under "Retrieved excerpts considered" rather
  than the number a user is meant to trust.

Two tools, two scales, and the same principle underneath both: pick the
retrieval strategy the actual document set justifies, not the one that's
fashionable to demo.

### Small model tier

`claude-haiku-4-5`, set as `DEFAULT_MODEL` in [`app/llm.py`](app/llm.py) and
marked with a `>>> SWAP POINT <<<` comment. It is the cheapest and lowest-latency
tier, which keeps the demo snappy — scenario generation asks for 3-5 scenarios in
one call and is the slow step.

Scoring is the most nuanced call in the app, so it is the first thing to promote
if feedback starts reading generic: change one constant to `claude-sonnet-5` or
`claude-opus-5`. `complete_json()` already accepts a per-call `model=`, so
splitting generation and scoring across tiers is a small change rather than a
refactor.

### SQLite locally, Postgres in production — same ORM code either way

One file, no external service, nothing to configure before running locally.
The ORM layer is standard SQLAlchemy 2.x with no SQLite-specific queries
anywhere, so moving to Postgres for a real deployment needed zero model or
query changes — only `app/database.py`'s engine setup, which now branches on
whether `DATABASE_URL` is set. See "Deploying to Vercel" above: this isn't
hypothetical, it's what a Vercel deployment actually needs, because
serverless functions have no persistent local disk for a SQLite file to live
on between requests.

### Server-rendered Jinja2, no build step

`git clone` → `pip install` → run. No `npm install`, no bundler, no toolchain that
can rot between when this was written and when it is reviewed. For a prototype
whose job is to demonstrate a product mechanic, a build step is pure risk.

### Two SDK constraints worth flagging

Both are easy to get wrong and were verified against the pinned versions:

- **`anthropic` 1.x removed `temperature` / `top_p` / `top_k`** from the SDK
  signature — passing `temperature=0` raises `TypeError`. If per-call determinism
  is ever needed it goes through `extra_body`, not a keyword argument.
- **Haiku 4.5 rejects `output_config.effort`** and predates adaptive thinking, so
  neither `output_config` nor `thinking` is sent on these calls. Both become
  available if you promote the model tier.

### Failure handling

Every model call is wrapped with a 30s timeout and translated into a single
`LLMError` carrying a user-safe message; routes render `error.html`. A missing
API key does not prevent the app from booting — it produces a clear, actionable
message. Model JSON is extracted with a bracket-matching scanner that tolerates
markdown fences and stray prose (and correctly ignores braces inside quoted
policy excerpts), with one corrective retry before failing. **A failed API call
never surfaces as a stack trace.**

---

## What I'd do with more time

- **A real vector index for the library once it stops being small.** The linear
  cosine-similarity scan in `app/rag.py` is the right call at "a handful of
  documents" — it stops being the right call once a client's library grows past
  that, at which point it's a swap to pgvector or a dedicated vector store, not
  a rewrite of the chunking or prompting.
- **A retrieval-quality eval set.** `addressed` now drives the UI instead of
  raw similarity (see above), so the sharper remaining gap isn't the
  confidence thresholds — it's not knowing how often retrieval hands the
  model the wrong chunks entirely, silently, before `addressed` ever gets a
  chance to catch it. Needs a labelled set of (question, expected source
  chunk) pairs, the same gap called out below for the scoring bands.
- **Audit trail for library questions.** The quiz keeps every `Response`;
  `/library/ask` currently doesn't persist a `Query` row, so there's no record
  of what was asked or what was retrieved for it — the same defensibility
  argument that drives the quiz's audit trail applies here too.
- **Auth and roles** — employee vs. compliance-manager views; right now anyone
  with a policy ID can see its results.
- **Admin view of aggregate compliance gaps.** The highest-value extension and
  the most natural fit with the core product: scoring free-text reasoning across
  a workforce reveals *which specific provisions* people systematically
  misunderstand. That is exactly the signal Tai needs to auto-assign remedial
  course content — the simulator becomes a diagnostic that feeds the tutor.
- **Multi-policy support** — one org, several policies, versioned, with
  re-assessment when a policy is revised.
- **Audit-log export** for compliance teams — timestamped, immutable records of
  who was assessed against which policy version, with the cited sections, in a
  format that can be handed to a regulator.
- **Evaluation harness for the grader.** The scoring prompt is the product; it
  needs a labelled set of answers with expected bands so prompt changes can be
  regression-tested rather than eyeballed.

---

## Project layout

```
app/
  main.py         FastAPI routes for the quiz loop
  library.py      FastAPI routes for the policy library (/library)
  database.py     engine, session, init — SQLite locally, Postgres if DATABASE_URL is set
  models.py       Policy / Scenario / Response, LibraryDocument / LibraryChunk
  llm.py          Anthropic wrapper, JSON extraction, DEMO_MODE
  rag.py          chunking, Voyage AI embedding, retrieval, library answers
  ratelimit.py    rate limiting for paid-API routes, via Upstash Redis
  templating.py   shared Jinja2Templates instance + section_tag filter + render_error
  text_utils.py   shared upload helpers: PDF/.txt/.md extraction, filename titles
  prompts.py      all prompts
  generation.py   policy -> scenarios
  scoring.py      answer -> assessment
  fixtures.py     canned demo-mode payloads
templates/        base, upload, quiz, results, error, library
static/style.css  single stylesheet
samples/          one realistic sample policy
vercel.json       Vercel function config (maxDuration) — see "Deploying to Vercel"
```
