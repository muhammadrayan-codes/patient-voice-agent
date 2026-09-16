# Voice AI Patient Registration System

A phone-based AI agent that conversationally collects U.S. patient demographic
information, persists it to a database, and exposes it via a REST API + dashboard.

**Live demo:**
- Phone number: `+1 (701) 977 9203`
- API base URL: `https://web-production-575b9.up.railway.app`
- Dashboard: `https://web-production-575b9.up.railway.app/dashboard`
- API docs (auto-generated): `https://web-production-575b9.up.railway.app/docs`

---

## Architecture
Caller (phone)
│
▼
Vapi (telephony + STT/TTS + LLM orchestration)
│ tool calls (HTTPS webhooks)
▼
FastAPI backend ──────► SQLite / Postgres
│
▼
REST API ◄──── Dashboard (static HTML, fetches /patients)

**Separation of concerns:**
- **Telephony + voice**: Vapi handles the phone number, speech-to-text,
  text-to-speech, and turn-taking. It runs the LLM conversation and calls
  our backend as "tools" (function calls) when it needs to look up or save data.
- **Conversation logic**: lives entirely in the Vapi system prompt
  (`vapi/assistant-config.json`) — not hardcoded in the backend. The backend
  has no awareness of "conversation state"; it just validates and persists
  whatever the LLM sends it, the same as if a human filled out a web form.
- **Data layer**: SQLModel (Pydantic + SQLAlchemy) models in `app/models.py`,
  enforcing types and validation rules server-side, independent of whatever
  the voice agent already checked.
- **API layer**: FastAPI routes in `app/main.py`. The `/vapi/tools/*` webhook
  routes are thin adapters that translate Vapi's tool-call JSON shape into
  calls against the *same* Pydantic models and DB session used by the public
  REST endpoints — there's one source of truth for validation, not two.

## Why this stack

- **FastAPI**: async-native, automatic OpenAPI docs (`/docs`), and Pydantic
  validation baked in — fastest path to a spec-compliant REST API in the
  time available.
- **SQLModel**: lets one class define both the DB table and the
  request/response schema, instead of hand-writing separate SQLAlchemy models
  + Pydantic schemas + mapping code between them.
- **SQLite by default**: zero setup, file-based, good enough for an
  assessment. Swap to Postgres by setting `DATABASE_URL` — no code changes
  needed (see Trade-offs below on why this matters for deployment).
- **Vapi**: abstracts STT/TTS/telephony so the actual engineering effort goes
  into the system prompt and tool design, which is what's actually being
  evaluated per the assessment's own FAQ.

## Data model

See `app/models.py`. Enforces (server-side, regardless of what the voice
agent already validated):
- Names: 1–50 chars, letters/hyphens/apostrophes only
- DOB: valid date, not in the future
- Sex: one of the four allowed enum values
- Phone numbers: normalized to 10 digits
- State: real 2-letter US abbreviation
- ZIP: 5-digit or ZIP+4
- Email: basic format check, optional
- `patient_id` (UUID), `created_at`, `updated_at` are always server-generated,
  never trusted from client input
- Soft delete via `deleted_at` — `DELETE` never removes a row

## REST API

All responses use the envelope `{ "data": ..., "error": ... }`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/patients` | List patients. Filters: `?last_name=`, `?date_of_birth=`, `?phone_number=` |
| GET | `/patients/{id}` | Get one patient |
| POST | `/patients` | Create patient (201) |
| PUT | `/patients/{id}` | Partial update |
| DELETE | `/patients/{id}` | Soft delete |
| GET | `/dashboard` | Simple HTML dashboard listing all patients |
| GET | `/health` | Health check |

Plus two Vapi-specific webhook routes (`/vapi/tools/lookup_patient`,
`/vapi/tools/create_patient`) that Vapi calls mid-conversation — see below.

## Voice agent design

Full system prompt and tool schemas: `vapi/assistant-config.json`. The
assistant was built directly in the Vapi dashboard (Assistants → Create
Assistant → Function tools), not imported from this file — the JSON in this
repo is kept as accurate documentation of the live configuration (model,
prompt, voice, and tool schemas) so a reviewer can see the full prompt
engineering without needing dashboard access. Live model: GPT-4.1.

Key design decisions:

- **Lookup-before-create**: the agent calls `lookup_patient` as soon as it has
  a phone number, *before* collecting the rest of the info, so it can offer
  "looks like we have a record for you — update instead?" per the bonus spec.
- **Confirm-then-write**: `create_patient` is only called after the agent
  reads back the full collected record and gets explicit confirmation —
  this satisfies the "must confirm before saving" requirement structurally,
  not just as a prompt suggestion.
- **Field-level re-prompting**: the prompt instructs the LLM to validate
  DOB/phone/state/zip itself during conversation and re-ask just that field
  on error, rather than failing the whole call — the backend is a second,
  authoritative validation layer behind it.
- **Corrections & restarts**: explicit prompt instructions for "actually, my
  name is spelled..." (update just that field) vs. "start over" (discard
  and restart) so the conversation doesn't have to be linear.

A Squad was scaffolded in the Vapi dashboard while exploring configuration
options but is not used — a single assistant handles the entire registration
flow, connected directly to the phone number's inbound settings.

## Setup

### 1. Backend

```bash
git clone <your-repo-url>
cd patient-voice-agent
pip install -r requirements.txt
cp .env.example .env   # edit DATABASE_URL if using Postgres
python seed.py         # optional: adds 2 demo patients
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for interactive API docs, or
`http://localhost:8000/dashboard` for the patient list.

### 2. Deploy

Push to GitHub, then connect the repo in [Railway](https://railway.app):
- Railway auto-detects `Procfile` / `railway.json` and deploys.
- Set `DATABASE_URL` in Railway's environment variables if attaching a
  managed Postgres (recommended — see Trade-offs).
- Deployed here at `https://web-production-575b9.up.railway.app`.

### 3. Vapi setup

1. Create an account at [vapi.ai](https://vapi.ai).
2. Create a new assistant (Assistants → Create Assistant), set the model,
   first message, and system prompt as documented in
   `vapi/assistant-config.json`.
3. Add two **Function** tools — `lookup_patient` and `create_patient` — with
   the parameter schemas and server URLs shown in `vapi/assistant-config.json`,
   pointed at the deployed API's `/vapi/tools/lookup_patient` and
   `/vapi/tools/create_patient` endpoints.
4. Provision a phone number (Phone Numbers → Create Phone Number) and, under
   that number's **Inbound Settings**, set the **Assistant** field to this
   assistant.
5. Publish the assistant. Call the number to test.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | No | `sqlite:///./patients.db` | SQLAlchemy connection string |

No API keys are hardcoded anywhere in the repo; Vapi/LLM credentials live in
Vapi's own dashboard, not in this codebase, since the backend never calls an
LLM directly — Vapi does.

## Known limitations & trade-offs

- **SQLite on ephemeral disk**: if deployed to a platform without a
  persistent volume, the SQLite file can be wiped on redeploy. For a real
  submission, either (a) attach a Railway volume, or (b) set `DATABASE_URL`
  to Railway's managed Postgres addon — the code supports both with no
  changes. Documented here rather than silently risking data loss.
- **No auth on the REST API**: acceptable for this assessment's scope (per
  the FAQ, HIPAA/production-hardening is explicitly out of scope); a real
  system would need API auth and per-tenant scoping.
- **Duplicate detection is phone-number-only**: matches the bonus spec as
  written, but a production system would likely also fuzzy-match on
  name + DOB to catch typo'd phone numbers.
- **No automated test suite included** given the time budget — manual
  end-to-end testing was done against every endpoint and both Vapi webhook
  routes, plus a full live phone call that successfully registered a patient
  and confirmed on the dashboard. Automated tests would be the first thing
  added with more time.
- **Call transcript storage** (bonus) not implemented — noted as a Next Step.
- **Mid-call disconnects**: handled at the Vapi platform level (it manages
  the call session); on our side, since nothing is written to the DB until
  the final confirmed `create_patient` call, a dropped call simply results
  in no record being created — no partial/corrupt data risk.
- **Vapi free-tier call limits**: the free Vapi phone number has a 10
  outbound-call-per-day cap (calls placed *by* Vapi, e.g. via the dashboard's
  "Talk to Assistant" test feature) and a 10-concurrent-call account limit.
  This does not affect inbound calls — i.e. a reviewer dialing the number
  directly is unaffected.

## Next steps (with more time)

- Automated integration tests (pytest + httpx) for all REST + webhook routes
- Call transcript storage, linked to `patient_id`
- Fuzzy duplicate detection (name + DOB, not just phone)
- Multi-language support (prompt-level branch on "Hablo español")
- Rate limiting on the public API
- Structured JSON logging instead of stdout log lines