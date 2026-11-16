# myPOV Backend

Privacy-first, intention-driven social discovery and local venue attribution backend built with **Django** and **Django REST Framework**.

Instead of public profiles, permanent photo feeds, or infinite swiping, `myPOV` connects verified people around **6 canonical real-world activity buckets** and **3 ephemeral time windows** inside vetted local venues—combining zero-retained-media AI intent extraction, hardware-bound anti-abuse reputation, live "What's the Vibe?" venue radar telemetry, 3–6 person Micro-Tables, Table-QR ROI attribution, and mutual-consent E2EE chat.

---

## Architecture & Core Invariants

1. **Zero Public Profile & Zero-Retained Biometrics (`users`)**
   - Accounts are username/pseudonym-first (`ephemeral_handle`) with no public bio, follower feed, or stored profile picture.
   - Liveness/18+ selfies submitted to `/api/users/zk-verify-liveness/` are validated and processed strictly in RAM (`del` buffer in `finally:`), writing zero files to disk or `MEDIA_ROOT`. Only boolean/enum verification claims and a one-way HMAC/SHA-256 `verification_proof_token` are stored.
2. **Hardware-Bound Anti-Abuse (`HardwareReputationLedger`)**
   - Users can execute a **One-Tap Account Wipe** (`/api/users/one-tap-wipe/`) at any time to atomically purge their account, intentions, matches, Micro-Table seats, reveal consents, and chat messages.
   - To prevent ban evasion, `HardwareReputationLedger` persists independently indexed solely by an irreversible `HMAC-SHA256(DEVICE_HASH_PEPPER, raw_device_or_phone_fingerprint)`. Accumulating `>= 3` distinct reporter strikes via `/api/users/report/` blacklists the hardware fingerprint (`403 Forbidden` on subsequent registration or verification attempts, even after wiping).
3. **Ephemeral Zero-Media Intentions (`posts`)**
   - Intentions are normalized into **6 canonical activity buckets** (`COFFEE_COWORK`, `DRINKS_NIGHTLIFE`, `SPORTS_PADEL`, `FOOD_DINING`, `BOARD_GAMES_GEEK`, `WALK_BEACH_SUNSET`) and **3 time windows** (`RIGHT_NOW`, `TONIGHT`, `THIS_WEEKEND`) to prevent liquidity fragmentation.
   - Optional camera frames sent to `/api/posts/extract-intent/` are processed in memory and immediately discarded; `Post.save()` enforces that zero binary image media is persisted.
4. **Live Venue Radar, Flash Perks & Table-QR Attribution (`venues`)**
   - `/api/venues/discover/` ranks active venues deterministically by bucket & tag overlap, live 2-hour decaying `VenueVibeReport` telemetry, active `VenueFlashPerk` availability, and strictly capped `Venue.tier` weights (paid boosts never override a bucket mismatch).
   - Physical Table-QR check-ins (`/api/venues/qr-checkin/`) atomically redeem flash perks (`select_for_update()`), record `VenueWalkInRedemption` revenue attribution, trigger `upgrade_prompt_eligible=True` when `FREE` tier venues reach 5 monthly walk-ins, and mark safe arrival on `Match` / `MicroTableSeat` records.
5. **Micro-Tables, Safety-Gated 1-on-1 Matching & Mutual-Consent E2EE Chat (`matches`, `chat`)**
   - 1-on-1 matching enforces mutual `match_safety_filter` compatibility (`ANY`, `VERIFIED_ONLY`, `SAME_GENDER_ONLY`, `COMMUNITY_ONLY`), block/report exclusion, bucket + city + time-window overlap, and automatically attaches a top-ranked public `suggested_venue`.
   - **Micro-Tables** let 3–6 eligible users claim seats atomically (`FORMING` → `LOCKED_READY` at `min_seats`, `409 Conflict` at `max_seats`) and auto-provision an encrypted group chat room.
   - **Two-Way Mutual Identity Reveal** masks real usernames (`username: None`, `is_revealed: False`) in all room/message serializers and Pusher broadcasts until **both** participants call `/api/chat/rooms/<room_id>/request-reveal/`.

---

## Repository Structure

```text
.
├── MYPOVREDESIGN.md            # Redesign specification (Prompts 1–4)
├── README.md
└── backend/
    ├── manage.py
    ├── pytest.ini
    ├── requirements.txt
    ├── socialapp/              # Django project settings & root URL routing
    │   ├── settings.py
    │   └── urls.py
    ├── users/                  # ZK liveness, safety filters, reporting, ledger, one-tap wipe
    ├── venues/                 # Venue discovery radar, vibe telemetry, flash perks, QR check-ins, ROI
    ├── posts/                  # 6 canonical buckets, 3 time windows, in-memory tag extraction
    ├── matches/                # Safety-gated 1-on-1 matching, Micro-Tables, auto-table matchmaker
    └── chat/                   # E2EE messages, mutual-consent identity reveal, Pusher integration
```

---

## Local Setup & Quickstart

### 1. Create a Virtual Environment & Install Dependencies

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate

# Core backend + test dependencies
pip install django djangorestframework djangorestframework-simplejwt pillow requests pusher pytest pytest-django

# Optional: full requirements (includes PyTorch/YOLO/Transformers/Postgres driver)
# pip install -r requirements.txt
```

### 2. Environment Variables

By default, local development and tests run with **SQLite** (`backend/db.sqlite3`) and deterministic local verifiers without requiring external services.

| Variable | Default | Description |
| --- | --- | --- |
| `DEVICE_HASH_PEPPER` | dev fallback string | Secret pepper for `HMAC-SHA256` device/phone fingerprint hashing |
| `ZK_LIVENESS_VERIFIER` | `users.verification.DeterministicLocalLivenessVerifier` | Pluggable Python dotted path for liveness/18+ verification provider |
| `USE_POSTGRES` | unset (`false`) | Set to `1` / `true` to use PostgreSQL instead of SQLite |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | `my_pov`, `postgres`, `""`, `localhost`, `5432` | PostgreSQL connection parameters when `USE_POSTGRES=true` |
| `PUSHER_APP_ID`, `PUSHER_KEY`, `PUSHER_SECRET`, `PUSHER_CLUSTER` | `""`, `""`, `""`, `"eu"` | Pusher credentials (no-ops cleanly when unset) |
| `GEMINI_API_KEY` | `""` | Optional Gemini API key for legacy `matches.controllers` |

### 3. Apply Migrations & Seed Cyprus Anchor Venues

```bash
cd backend
python manage.py migrate
python manage.py seed_cyprus_venues
python manage.py runserver
```

`seed_cyprus_venues` idempotently seeds **15 anchor venues** across `LIMASSOL_OLD_TOWN_SARIPOLOU` (5), `LIMASSOL_MOLOS` (5), and `PAPHOS_KATO` (5), each with `VenueTableQR` tokens, active 2-hour `VenueVibeReport` telemetry, and `VenueFlashPerk` offers.

---

## API Reference

All API routes are mounted under `/api/` (with legacy compatibility aliases under `/users/`, `/posts/`, `/matches/`, `/chat/`). Authenticated endpoints accept JWT Bearer tokens (`Authorization: Bearer <access>`).

### Users (`/api/users/`)

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/api/users/signup/` | Public | Creates a temporary 24h account (`username`, `password`, optional `email`, `ephemeral_handle`, `device_fingerprint`). Rejects with `403` if device is blacklisted/cooled down. |
| `POST` | `/api/users/login/` | Public | Authenticates credentials, checks expiry & hardware blacklist (`403`), and returns JWT `access`/`refresh` tokens. |
| `POST` | `/api/users/forgot_password/` | Public | Sends password-reset OTP email for active accounts. |
| `POST` | `/api/users/zk-verify-liveness/` | JWT | Validates in-memory selfie magic bytes + `attestation_token` / `device_token`, discards bytes from RAM, sets `is_verified_human` & `is_verified_adult_18_plus`. Returns `403` if blacklisted. |
| `GET`, `PATCH` | `/api/users/safety-preferences/` | JWT | Reads or updates `match_safety_filter` (`ANY`, `VERIFIED_ONLY`, `SAME_GENDER_ONLY`, `COMMUNITY_ONLY`), `e2ee_public_key`, `verified_gender_group`, `ephemeral_handle`, and domain-only `community_email` extraction. |
| `POST` | `/api/users/report/` | JWT | Submits a `SafetyReport` (`HARASSMENT`, `NO_SHOW`, `UNSAFE_BEHAVIOR`, `SPAM`), blocks any `Match` between the pair, and blacklists the hardware ledger at `>= 3` distinct reporter strikes. |
| `POST` | `/api/users/one-tap-wipe/` | JWT | Atomically deletes the user, posts, matches, Micro-Table seats, reveal consents, and chat rooms/messages while incrementing `HardwareReputationLedger.wipe_count_30d`. |

### Venues (`/api/venues/`)

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/api/venues/discover/` | Public | Ranks active venues by `city`, `bucket`, comma-separated `tags`, and optional `lat`/`lng`, aggregating active 2-hour `VenueVibeReport`s and active `VenueFlashPerk`s. |
| `POST` | `/api/venues/<venue_id>/vibe/` | JWT | Submits pre-selected enum vibe telemetry (`crowd_level`, `noise_level`, `current_best_for`, `vibe_preset_badge`). Rejects free-text keys (`400`) and rate-limits to 1 report/user/venue/60 min (`429`). |
| `POST` | `/api/venues/qr-checkin/` | JWT | Validates `qr_secret_token`, atomically claims an active `VenueFlashPerk` (`409` on explicit cap exhaustion), logs `VenueWalkInRedemption`, marks safe arrival on `match_id` / `micro_table_id`, and optionally records inline `vibe`. |
| `GET` | `/api/venues/<venue_id>/roi-summary/` | JWT | Returns `views`, `intention_matches_routed`, `verified_walk_ins`, `monthly_verified_walk_ins`, `total_party_size_seated`, `estimated_revenue_eur`, and `upgrade_prompt_eligible`. |

### Posts / Ephemeral Intentions (`/api/posts/`)

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/api/posts/extract-intent/` | JWT | Accepts optional in-memory `image` + `text` (`<= 140` chars) + optional `activity_bucket`; returns `suggested_bucket` and 3–5 canonical `suggested_tags` while writing zero files to disk. |
| `POST` | `/api/posts/confirm-intent/` | JWT | Persists a zero-media intention (`activity_bucket`, `time_window`, 1–5 `confirmed_tags`, `vibe_preference`, `city`, optional `preferred_venue_id`) with computed `expires_at` and returns top 5 ranked `recommended_venues`. |
| `GET`, `POST` | `/api/posts/posts/` | JWT | Lists non-expired active intentions for non-expired active users. |

### Matches & Micro-Tables (`/api/matches/`)

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/api/matches/micro-tables/` | JWT | Lists active `FORMING` and `LOCKED_READY` Micro-Tables filtered by `city`, `activity_bucket`, and caller's safety/verification eligibility. |
| `POST` | `/api/matches/micro-tables/<table_id>/claim-seat/` | JWT | Atomically claims a seat (`select_for_update()`), transitions `FORMING` → `LOCKED_READY` and provisions a group `Chat` room once `min_seats` is reached, or returns `409 Conflict` when full. |
| `POST` | `/api/matches/generate/` | JWT | Runs safety-gated 1-on-1 matching (`generate_ai_matches_for_user`) and `run_auto_table_matchmaker` for `SMALL_GROUP_3_6` / `EITHER` intentions. |
| `GET` | `/api/matches/ai-matches/` | JWT | Lists the caller's `pending_ai` and `matched` 1-on-1 matches including `suggested_venue`. |
| `POST` | `/api/matches/ai-matches/<match_id>/interact/` | JWT | `accept` or `decline` an AI match (`accept` provisions the 1-on-1 `Chat` room). |

### E2EE Chat & Mutual Reveal (`/api/chat/`)

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/api/chat/rooms/` | JWT | Lists non-expired 1-on-1 and Micro-Table chat rooms for the authenticated user. |
| `GET` | `/api/chat/rooms/<room_id>/` | JWT | Returns room participants (ephemeral handles, verification badge, confirmed tags, `e2ee_public_key`, masked usernames until mutual consent) and messages (`room_id` accepts `room_uuid` or integer `id`). |
| `POST` | `/api/chat/rooms/<room_id>/messages/` | JWT | Sends an E2EE payload (`ciphertext`, `nonce`) or `content` and triggers a masked Pusher `new_message` event. |
| `POST` | `/api/chat/rooms/<room_id>/request-reveal/` | JWT | Marks caller's `consented_to_reveal=True` (with optional `target_user_id` for Micro-Tables); real usernames unlock only once both participants mutually consent. |

---

## Management Commands

| Command | Description |
| --- | --- |
| `python manage.py seed_cyprus_venues` | Idempotently seeds 15 Limassol & Paphos anchor venues, table QRs, live vibe reports, and flash perks. |
| `python manage.py cleanup_expiry_data` | Deletes expired `Post` intentions and deactivates expired `CustomUser` accounts. |
| `python manage.py cleanup_expired_chats` | Purges expired ephemeral `Chat` rooms and `ChatMessage` rows and marks expired `MicroTable`s `EXPIRED`. |
| `python manage.py generate_matches` | Runs `generate_all_ai_matches()` across active users. |

---

## Running Tests & Checks

```bash
cd backend
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/pytest -v
```

The test suite (`21` tests across `users/tests.py`, `venues/tests.py`, `posts/tests.py`, `matches/tests.py`, and `chat/tests.py`) covers:
- ZK liveness verification with zero media writes to disk and magic-byte validation
- Safety preferences update and domain-only community extraction
- Atomic One-Tap Account Wipe and 3-strike hardware blacklist persistence across wipes
- Bucket-gated venue discovery ranking, 2-hour vibe telemetry decay, 60-minute rate limiting, atomic QR flash perk cap enforcement, and ROI / free-tier upgrade eligibility
- In-memory intent extraction with zero disk writes, time-window expiry (`RIGHT_NOW`, `TONIGHT`, `THIS_WEEKEND`), and `cleanup_expiry_data`
- Safety-filtered 1-on-1 matching with `suggested_venue`, atomic Micro-Table seat claiming (`409` on capacity), Auto-Table Matchmaker, Venue QR safe arrival check-ins, E2EE chat mutual-consent reveal gate, and `cleanup_expired_chats`.
