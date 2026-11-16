## Prompt 1 — Zero-Knowledge Identity, Hardware Anti-Abuse & One-Tap Account Wipe (`users`)

# ROLE & OBJECTIVE
You are upgrading the `backend/` Django REST Framework codebase of `myPOV` (apps: `users`, `posts`, `matches`, `chat`, `socialapp`). Do NOT touch or create any frontend code.

In **Prompt 1**, upgrade the `users` app to implement **Zero-Knowledge Privacy & Cryptographic Verification**, **Hardware-Bound Anti-Abuse Reputation**, **Safety Matching Preferences**, and **One-Tap Account Wipe**.

## 1. ARCHITECTURAL INVARIANTS
1. **Username-First, Zero Public Profile:** Users have no public bio, follower feed, or stored profile picture.
2. **Zero-Retained Biometrics / ID Media:** When a user completes liveness/age verification, any selfie or frame uploaded is processed strictly in memory and discarded immediately (`del` buffer, zero disk writes). Only boolean/enum verification claims (`is_verified_human`, `is_verified_adult_18_plus`, `verified_attribute_group`, `verified_community_tag`) and a signed/hashed verification proof token are stored.
3. **Hardware-Bound Anti-Abuse (Ban Evasion Protection):** Users can execute a **One-Tap Account Wipe** at any time to permanently purge their account, intentions, matches, and chat messages. However, to prevent bad actors from wiping their account to evade harassment bans, the backend maintains a separate `HardwareReputationLedger` indexed ONLY by an irreversible HMAC-SHA256 hash (`HMAC(settings.DEVICE_HASH_PEPPER, raw_device_or_phone_fingerprint)`).
4. **Safety & Comfort Filters:** Users can restrict who they match with (`MATCH_FILTER_ANY`, `MATCH_FILTER_VERIFIED_HUMANS_ONLY`, `MATCH_FILTER_SAME_VERIFIED_GENDER`, `MATCH_FILTER_VERIFIED_COMMUNITY_ONLY`).

## 2. REQUIRED DATA MODEL & MIGRATION CHANGES (`backend/users/models.py`)
1. Extend the user model (or `UserProfile` linked 1-to-1 with User, preserving existing `users` fields/migrations cleanly):
   - `ephemeral_handle` (CharField, unique, auto-generated or user-chosen pseudonym)
   - `is_verified_human` (BooleanField, default=False)
   - `is_verified_adult_18_plus` (BooleanField, default=False)
   - `verified_gender_group` (CharField choices: `UNSPECIFIED`, `WOMAN`, `MAN`, `NON_BINARY`, default=`UNSPECIFIED` — used strictly for opt-in safety filtering, never publicly displayed)
   - `verified_community_domain` (CharField, blank=True, e.g., `cut.ac.cy`, `ucy.ac.cy`, `expat_verified` — extracted from one-time email/code check without storing the raw email in plain text)
   - `match_safety_filter` (CharField choices: `ANY`, `VERIFIED_ONLY`, `SAME_GENDER_ONLY`, `COMMUNITY_ONLY`, default=`VERIFIED_ONLY`)
   - `device_fingerprint_hmac` (CharField, max_length=64, db_index=True)
   - `e2ee_public_key` (TextField, blank=True — client public key for E2E encrypted chat)
2. Create `HardwareReputationLedger`:
   - `fingerprint_hmac` (CharField, max_length=64, unique=True, db_index=True)
   - `strike_count` (PositiveIntegerField, default=0)
   - `is_blacklisted` (BooleanField, default=False)
   - `cooldown_until` (DateTimeField, null=True, blank=True)
   - `wipe_count_30d` (PositiveIntegerField, default=0)
   - `updated_at` (DateTimeField, auto_now=True)
3. Create `SafetyReport`:
   - Reporter FK, reported user FK (nullable on SET_NULL so audit survives account wipe), `reported_fingerprint_hmac` (CharField), `reason` (`HARASSMENT`, `NO_SHOW`, `UNSAFE_BEHAVIOR`, `SPAM`), `created_at`.
   - When a user accumulates >= 3 distinct reporter strikes, automatically set `is_blacklisted=True` on their `HardwareReputationLedger`.

## 3. ENDPOINTS TO IMPLEMENT (`backend/users/`)
- `POST /api/users/zk-verify-liveness/`: Accepts an in-memory selfie image + signed attestation/device token, validates image magic bytes in memory, computes liveness/adult verification (pluggable provider with deterministic local verifier for tests), immediately discards image bytes from RAM, and sets `is_verified_human=True` and `is_verified_adult_18_plus=True`. Rejects with `403` if `HardwareReputationLedger.is_blacklisted=True`.
- `PATCH /api/users/safety-preferences/`: Updates `match_safety_filter`, `e2ee_public_key`, and opt-in safety filter attributes.
- `POST /api/users/report/`: Submits a `SafetyReport` against a matched user, increments `HardwareReputationLedger.strike_count`, and immediately blocks/unmatches the pair.
- `POST /api/users/one-tap-wipe/`: Atomically deletes all user posts/intentions, active matches, chat rooms/messages, and the user account itself, while preserving only the non-reversible `HardwareReputationLedger` entry (incrementing `wipe_count_30d`).

## 4. VERIFICATION
Write comprehensive `pytest` / Django test cases in `backend/users/tests.py` verifying:
1. Liveness verification sets `is_verified_human=True` and writes zero image files to disk/media storage.
2. One-tap wipe completely removes the user, their posts, matches, and chats from the database.
3. A user who receives 3 safety strikes and then runs `one-tap-wipe` is still blocked (`403`) when attempting to register or verify again with the same device/phone fingerprint.
Run the test suite and confirm 100% pass before finishing.

---

## Prompt 2 — Venues, Live "What's the Vibe" Telemetry, Flash Perks & Table QR Attribution (`venues`)

# ROLE & OBJECTIVE
In **Prompt 2**, create a new Django app `backend/venues/` and wire it into `socialapp/settings.py` and `socialapp/urls.py`. Do NOT touch any frontend code.

This app solves the **Months 2–7 single-player utility hook** and **B2B venue ROI attribution** by turning the map into a live "What's the Vibe?" radar, managing dynamic off-peak Flash Perks, and tracking physical Table-QR group walk-ins.

## 1. MODELS TO IMPLEMENT (`backend/venues/models.py`)
1. `Venue`:
   - `id` (UUIDField, primary_key=True)
   - `name`, `slug`, `city` (choices: `LIMASSOL`, `PAPHOS`, `NICOSIA`), `district_corridor` (e.g., `LIMASSOL_OLD_TOWN_SARIPOLOU`, `LIMASSOL_MOLOS`, `PAPHOS_KATO`, `NICOSIA_CENTER`)
   - `latitude` (DecimalField), `longitude` (DecimalField), `address` (CharField)
   - `supported_buckets` (JSONField — list of canonical activity buckets this venue fits, e.g., `["COFFEE_COWORK", "DRINKS_NIGHTLIFE", "BOARD_GAMES_GEEK"]`)
   - `interest_tags` (JSONField — granular tags for ranking fit, e.g., `["specialty_coffee", "quiet_wifi", "craft_beer", "padel_courts"]`)
   - `tier` (choices: `FREE`, `STARTER_29`, `SMALL_59`, `BIG_129`, `ENTERPRISE`, default=`FREE`)
   - `is_weekly_map_anchor` (BooleanField, default=False)
   - `default_discount_text` (CharField, blank=True, e.g., `"10% off for myPOV check-ins"`)
   - `avg_spend_estimate_eur` (DecimalField, default=15.00 — used to compute venue ROI on walk-ins)
   - `is_active` (BooleanField, default=True)
2. `VenueTableQR`:
   - `id` (UUIDField, primary_key=True)
   - `venue` (FK to `Venue`, related_name=`table_qrs`)
   - `table_label` (CharField, e.g., `"Table 4"`, `"Bar Counter"`)
   - `qr_secret_token` (CharField, unique=True, db_index=True — signed/rotatable token encoded in the physical table QR)
   - `is_active` (BooleanField, default=True)
3. `VenueVibeReport` (Pre-selected "What's the Vibe?" telemetry):
   - `venue` (FK to `Venue`, related_name=`vibe_reports`)
   - `user` (FK to User, on_delete=SET_NULL, null=True)
   - `crowd_level` (choices: `QUIET`, `MODERATE`, `LIVELY`, `PACKED`)
   - `noise_level` (choices: `STUDY_QUIET`, `CONVERSATIONAL`, `LOUD_MUSIC`)
   - `current_best_for` (choices: `COFFEE_COWORK`, `DRINKS_NIGHTLIFE`, `SPORTS_PADEL`, `FOOD_DINING`, `BOARD_GAMES_GEEK`, `WALK_BEACH_SUNSET`)
   - `vibe_Preset_badge` (choices: `LAPTOP_FRIENDLY`, `GREAT_MUSIC`, `FAST_SERVICE`, `SOCIAL_CROWD`, `CHILL_TERRACE`)
   - `created_at` (DateTimeField, auto_now_add=True)
   - `expires_at` (DateTimeField — defaults to `created_at + 2 hours` so live vibe telemetry auto-decays)
4. `VenueFlashPerk` (Off-peak capacity drops):
   - `venue` (FK to `Venue`, related_name=`flash_perks`)
   - `title` (CharField, e.g., `"2-for-1 Iced Flat White for Study Buddies"`)
   - `target_bucket` (CharField — matches canonical activity bucket)
   - `valid_from` (DateTimeField), `valid_until` (DateTimeField)
   - `max_redemptions` (PositiveIntegerField, default=20), `redeemed_count` (PositiveIntegerField, default=0)
   - `requires_companion_or_table` (BooleanField, default=False)
5. `VenueWalkInRedemption` (Verified "Bums on Seats" attribution):
   - `venue` (FK to `Venue`), `table_qr` (FK to `VenueTableQR`)
   - `user` (FK to User, on_delete=SET_NULL, null=True)
   - `match_id` (UUIDField, null=True, blank=True — links to 1-on-1 match or Micro-Table if applicable)
   - `party_size` (PositiveSmallIntegerField, default=1)
   - `estimated_revenue_eur` (DecimalField)
   - `created_at` (DateTimeField, auto_now_add=True)

## 2. SERVICES & ENDPOINTS (`backend/venues/`)
1. **Venue Ranking & Live Vibe Aggregation (`GET /api/venues/discover/`):**
   - Query params: `city`, `bucket`, `tags` (comma-separated), `lat`, `lng`.
   - Ranks active venues deterministically by:
     1. Bucket & tag overlap score with the user's current intention.
     2. Active non-expired `VenueVibeReport` aggregation (last 2 hours: dominant `crowd_level`, `noise_level`, `vibe_preset_badge`, and active check-in count).
     3. Active `VenueFlashPerk` availability.
     4. Paid `Venue.tier` boost weight (`FREE` < `STARTER_29` < `SMALL_59` < `BIG_129`), strictly capped so irrelevant venues are never boosted into an unrelated activity bucket.
2. **Pre-Select "What's the Vibe?" Submission (`POST /api/venues/<venue_id>/vibe/`):**
   - Accepts strictly pre-selected enum choices (`crowd_level`, `noise_level`, `current_best_for`, `vibe_preset_badge`)—zero free-text spam. Rate-limited to 1 report per user per venue per 60 minutes.
3. **Table QR Scan & Safe Arrival / Perk Redemption (`POST /api/venues/qr-checkin/`):**
   - Accepts `qr_secret_token`, optional `match_id` / `micro_table_id`, and optional inline `vibe` pre-select payload.
   - Creates a `VenueWalkInRedemption`, increments any active `VenueFlashPerk.redeemed_count` atomically (`select_for_update()`), and if `Venue.tier == FREE` and the venue crosses 5 verified walk-ins in the current calendar month, sets a flag `upgrade_prompt_eligible=True` in the venue analytics summary.
4. **Venue ROI Dashboard Endpoint (`GET /api/venues/<venue_id>/roi-summary/`):**
   - Returns views, intention matches routed to the venue, verified QR table walk-ins, total party size seated, and `estimated_revenue_eur` generated.
5. **Seed Command (`python manage.py seed_cyprus_venues`):**
   - Idempotently seeds 15 realistic anchor venues across Limassol (`LIMASSOL_OLD_TOWN_SARIPOLOU`, `LIMASSOL_MOLOS`) and Paphos (`PAPHOS_KATO`) with table QRs, active vibe reports, and flash perks.

## 3. VERIFICATION
Write automated tests in `backend/venues/tests.py` covering:
- Venue ranking by bucket + tag fit + tier boost (ensuring paid boost never overrides bucket mismatch).
- 2-hour decay of `VenueVibeReport` telemetry.
- Atomic Table QR check-in, `VenueFlashPerk` redemption cap enforcement, and ROI calculation.
Run all tests (`users` + `venues`) and confirm 100% pass.

---

## Prompt 3 — Pre-Selected Activity Buckets, Time Windows & Ephemeral Zero-Retained-Media Posts (`posts`)

# ROLE & OBJECTIVE
In **Prompt 3**, upgrade `backend/posts/` so user intentions use **6 Canonical Pre-Selected Activity Buckets**, **3 Fixed Time Windows**, and **Zero-Retained-Media Tag Extraction**. Do NOT touch any frontend code.

This solves the **liquidity fragmentation problem** (preventing 50 active users in Limassol from splitting into 50 unmatchable free-text tags) while enforcing the investor teaser's core privacy rule: *"Photo or a line of text becomes interest tags after user confirmation. The original media is deleted."*

## 1. CANONICAL BUCKETS & TIME WINDOWS (`backend/posts/constants.py`)
Define the 6 canonical activity buckets and 3 time windows:
- `ActivityBucket`:
  1. `COFFEE_COWORK` ("Coffee, Study & Co-Work")
  2. `DRINKS_NIGHTLIFE` ("Drinks, Craft Beer & Nightlife")
  3. `SPORTS_PADEL` ("Sports, Gym, Running & Padel")
  4. `FOOD_DINING` ("Food, Street Food & Casual Dining")
  5. `BOARD_GAMES_GEEK` ("Board Games, Gaming & Trivia")
  6. `WALK_BEACH_SUNSET` ("Walk, Beach, Sunset & Explore")
- `TimeWindow`:
  1. `RIGHT_NOW` (Expires in 3 hours)
  2. `TONIGHT` (Expires at 02:00 local time)
  3. `THIS_WEEKEND` (Expires Sunday 23:59 local time)
- Define a curated dictionary of **Pre-Selected Micro-Activity Tags** per bucket (e.g., under `SPORTS_PADEL`: `["padel_doubles", "gym_lift", "5k_run", "football_5v5", "kickboxing_sparring"]`; under `COFFEE_COWORK`: `["deep_work_sprint", "exam_study", "specialty_coffee", "founder_chat"]`).

## 2. EPHEMERAL MEDIA -> TAG EXTRACTION PIPELINE (`backend/posts/`)
1. **Step 1 — In-Memory Tag Extraction (`POST /api/posts/extract-intent/`):**
   - Accepts an optional in-memory photo upload (`image`), an optional short text line (`max_length=140`), and an optional pre-selected `activity_bucket`.
   - Processes the image **strictly in memory** (using the existing YOLO/CLIP analyzer or deterministic tagger fallback when weights are offline) to suggest:
     - `suggested_bucket` (one of the 6 `ActivityBucket` values)
     - `suggested_tags` (3–5 canonical tags from the bucket taxonomy + detected object tags)
   - **CRITICAL PRIVACY INVARIANT:** The uploaded image buffer is closed and deleted in a `finally:` block before the HTTP response returns. No image file is ever saved to disk or `MEDIA_ROOT`.
2. **Step 2 — User Confirmation & Post Creation (`POST /api/posts/confirm-intent/`):**
   - Accepts:
     - `activity_bucket` (required `ActivityBucket`)
     - `time_window` (required `TimeWindow`: `RIGHT_NOW`, `TONIGHT`, `THIS_WEEKEND`)
     - `confirmed_tags` (list of 1–5 confirmed tags)
     - `vibe_preference` (pre-select: `CHILL_1ON1`, `SMALL_GROUP_3_6`, `EITHER`, default=`EITHER`)
     - `city` (`LIMASSOL`, `PAPHOS`, `NICOSIA`) and optional `preferred_venue_id`
   - Saves the `Post` (Intention) with `expires_at` computed from `time_window`, and immediately returns the top 5 ranked `Venue` recommendations for that bucket + tag set using the `venues` ranking service from Prompt 2.
3. Update `Post` model (`backend/posts/models.py`):
   - Make any legacy `image` field optional/null (`null=True, blank=True`) and enforce in `save()` that no binary image file is persisted on confirmed intent posts.
   - Add `activity_bucket`, `time_window`, `confirmed_tags` (JSONField), `vibe_preference`, `city`, `preferred_venue` (FK to `venues.Venue`, null=True), and `expires_at`.

## 3. VERIFICATION
Write automated tests in `backend/posts/tests.py` verifying:
1. `POST /api/posts/extract-intent/` with a test JPEG returns `suggested_bucket` and `suggested_tags` while writing **zero files** to disk.
2. `POST /api/posts/confirm-intent/` creates an active intention in one of the 6 canonical buckets, sets the proper `expires_at` based on `time_window`, and returns ranked local venues matching that bucket.
3. Expired intentions are excluded from active queries and cleaned up by `cleanup_expiry_data`.
Run all tests across `users`, `venues`, and `posts` and confirm 100% pass.

---

## Prompt 4 — "Micro-Tables" (3–6 Person Venue Drop-Ins), Safe 1-on-1 Matching & Mutual-Reveal Encrypted Chat (`matches` & `chat`)

# ROLE & OBJECTIVE
In **Prompt 4**, upgrade `backend/matches/` and `backend/chat/` to implement **Micro-Tables (3–6 person group drop-ins at partner venues)**, **Safety-Filtered 1-on-1 Intention Matching**, **Mutual-Consent Identity Reveal**, and **Venue-Locked Safe Check-Ins**. Do NOT touch any frontend code.

## 1. MICRO-TABLES ENGINE (`backend/matches/models.py` & `views.py`)
Micro-Tables solve the Month 2–7 cold-start and offline safety problem by letting 3–6 people with the same `activity_bucket` join a hosted table at a vetted public `Venue`.
1. Create model `MicroTable`:
   - `id` (UUIDField, primary_key=True)
   - `venue` (FK to `venues.Venue`, related_name=`micro_tables`)
   - `activity_bucket` (CharField — one of the 6 canonical buckets)
   - `time_window` (CharField — `RIGHT_NOW`, `TONIGHT`, `THIS_WEEKEND`)
   - `scheduled_for` (DateTimeField)
   - `min_seats` (PositiveSmallIntegerField, default=3), `max_seats` (PositiveSmallIntegerField, default=6)
   - `safety_mode` (choices: `OPEN`, `VERIFIED_HUMANS_ONLY`, `VERIFIED_WOMEN_ONLY`, `COMMUNITY_ONLY`, default=`VERIFIED_HUMANS_ONLY`)
   - `status` (choices: `FORMING`, `LOCKED_READY`, `CHECKED_IN`, `COMPLETED`, `EXPIRED`, default=`FORMING`)
   - `perk_description` (CharField, blank=True, e.g., `"15% off table bill when 3+ members scan table QR"`)
   - `chat_room_id` (UUIDField, null=True, blank=True)
2. Create model `MicroTableSeat`:
   - `table` (FK to `MicroTable`, related_name=`seats`)
   - `user` (FK to User), `post` (FK to `posts.Post`, null=True)
   - `ephemeral_alias` (CharField — e.g., `"Anon Fox"`, `"Anon Falcon"` within the table until mutual reveal)
   - `checked_in_via_qr` (BooleanField, default=False)
   - `joined_at` (DateTimeField, auto_now_add=True)
   - `unique_together = ('table', 'user')`
3. Micro-Table Lifecycle & Endpoints:
   - `GET /api/matches/micro-tables/`: Lists active `FORMING` and `LOCKED_READY` tables in the user's `city` and `activity_bucket` that satisfy the user's verification/safety eligibility.
   - `POST /api/matches/micro-tables/<table_id>/claim-seat/`:
     - Uses `transaction.atomic()` and `select_for_update()` on `MicroTable`.
     - Verifies the user meets `table.safety_mode` (e.g., if `VERIFIED_WOMEN_ONLY`, requires `user.is_verified_human=True` and `user.verified_gender_group == 'WOMAN'`) and is not blocked by any current seat holder.
     - Assigns a seat. As soon as `seats.count() >= table.min_seats`, transitions `table.status = 'LOCKED_READY'` and automatically provisions an encrypted group `ChatRoom`.
   - **Auto-Table Matchmaker:** When 3+ users in the same `city`, `activity_bucket`, and `time_window` have `vibe_preference` in (`SMALL_GROUP_3_6`, `EITHER`) and no existing table exists, automatically spawn a `MicroTable` at the highest-ranked partner `Venue` for that bucket.

## 2. SAFETY-GATED 1-ON-1 MATCHING & VENUE ESCROW (`backend/matches/`)
1. Upgrade the 1-on-1 matching service (`backend/matches/services.py` & `models.py`):
   - Two active `Post` intentions only match if:
     1. Same `city`, same `activity_bucket`, and overlapping `time_window`.
     2. Both users' `match_safety_filter` rules are mutually satisfied (e.g., `VERIFIED_ONLY`, `SAME_GENDER_ONLY`, `COMMUNITY_ONLY`) and neither user has blocked/reported the other.
     3. Tag cosine/Jaccard overlap exceeds threshold.
   - Every 1-on-1 `Match` automatically attaches a `suggested_venue` (FK to `venues.Venue` — the top-ranked public partner venue matching their shared tags) so first meetups always default to a safe public commercial space.
   - Add `user1_checked_in_at` and `user2_checked_in_at` (DateTimeField, null=True) on `Match`, updated when they scan the venue's `VenueTableQR` (`POST /api/venues/qr-checkin/`).

## 3. E2EE CHAT & MUTUAL-CONSENT IDENTITY REVEAL (`backend/chat/`)
1. Upgrade `ChatRoom` / `Match` reveal state (`backend/chat/models.py`):
   - By default, chat participants see only each other's `ephemeral_handle` (e.g., `@limassol_padel_42`), confirmed activity tags, verification badge (`Verified 18+ Human`), and each other's `e2ee_public_key` for end-to-end encrypted message payloads (`ciphertext`, `nonce`).
   - Add **Two-Way Mutual Identity Reveal**:
     - `POST /api/chat/rooms/<room_id>/request-reveal/`: Marks the calling participant as `consented_to_reveal=True`.
     - **STRICT INVARIANT:** Real display names / social handles are NEVER returned in any serializer or WebSocket/Pusher payload until **BOTH** participants in a 1-on-1 chat (or two specific members in a Micro-Table) have set `consented_to_reveal=True`.
2. Ensure `cleanup_expired_chats` purges expired ephemeral chat rooms and messages cleanly.

## 4. END-TO-END VERIFICATION
Write an end-to-end test suite in `backend/matches/tests.py` and `backend/chat/tests.py` verifying:
1. **Bucket + Safety Filter Matching:** Two users posting in `SPORTS_PADEL` (`TONIGHT`) with compatible safety filters match and receive a ranked `suggested_venue`; an unverified user is blocked from matching with a user who set `match_safety_filter=VERIFIED_ONLY`.
2. **Micro-Table Formation:** 3 verified users claiming seats at a `MicroTable` atomically transition it from `FORMING` to `LOCKED_READY` and open a group chat room; a 7th user attempting to join a 6-seat table receives `409 Conflict`.
3. **Mutual Reveal Gate:** Calling `GET /api/chat/rooms/<id>/` when only User A has consented to reveal masks User B's real identity; once User B also calls `request-reveal`, both identities are revealed.
4. **Venue QR Safe Arrival:** Scanning the `VenueTableQR` marks the match/table seat as checked-in and logs a verified walk-in in `VenueWalkInRedemption`.
Run the entire backend test suite (`users`, `venues`, `posts`, `matches`, `chat`) and confirm 100% pass with zero regressions.