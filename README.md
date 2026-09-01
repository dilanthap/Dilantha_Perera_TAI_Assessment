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

cp .env.example .env               # then add your key to .env
# ANTHROPIC_API_KEY=sk-ant-...

uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**, click **Load sample policy**, and run the loop.

### Try it without an API key

```bash
DEMO_MODE=1 uvicorn app.main:app --reload
```

Demo mode serves bundled sample assessments instead of calling the API — no key,
no network. The entire UI, database and scoring loop work identically. It exists
so you can see the app run before spending a key on it, and so the UI could be
tested independently of model behaviour during development.

A ready-made sample policy is in [`samples/northwind_ai_policy.md`](samples/northwind_ai_policy.md)
— a realistic one-page AI-usage policy for a fictional financial firm.

---

## The loop

```
Upload policy  ──▶  Generate scenarios  ──▶  Answer in free text  ──▶  Score reasoning  ──▶  Results
   (Policy)          (Scenario × 3-5)             (Response)            (0-100 + citation)
```

| File | Role |
|---|---|
| `app/main.py` | FastAPI routes; catches every `LLMError` and renders a clean error page |
| `app/llm.py` | Anthropic client, timeouts, error translation, JSON extraction, demo mode |
| `app/prompts.py` | Every prompt, isolated for reading and tuning |
| `app/generation.py` | Policy → grounded scenarios |
| `app/scoring.py` | Scenario + excerpt + answer → score, verdict, feedback, citation |
| `app/models.py` | `Policy` → `Scenario` → `Response` |

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

### No vector store — a considered choice, not a shortcut

The reflex for "answer questions about a document" is chunk → embed → retrieve
top-k. This app deliberately does not, because an AI-usage policy is a few pages
(the sample is ~1,100 tokens) and fits in the context window many times over.
Sending the whole document on every call buys three things chunked retrieval
would cost:

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

This stops working for a client with a library of long policies or a
hundred-page handbook. That is the first thing I'd build next (see below).

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

### SQLite + SQLAlchemy

One file, no external service, nothing to configure before running. The ORM layer
is standard SQLAlchemy 2.x, so the `DATABASE_URL` in `database.py` is the only
line that changes to move to Postgres.

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

- **Real chunked RAG** for large policy libraries — section-aware splitting so
  citations stay intact across chunk boundaries, plus a retrieval-confidence
  signal so "the policy is silent" stays distinguishable from "retrieval missed".
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
  main.py         FastAPI routes
  database.py     engine, session, init
  models.py       Policy / Scenario / Response
  llm.py          Anthropic wrapper, JSON extraction, DEMO_MODE
  prompts.py      all prompts
  generation.py   policy -> scenarios
  scoring.py      answer -> assessment
  fixtures.py     canned demo-mode payloads
templates/        base, upload, quiz, results, error
static/style.css  single stylesheet
samples/          one realistic sample policy
```
