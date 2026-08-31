# video-call-app

Self-hosted WebRTC video/voice calling for iLive, built against the "Command
Centre-Initiated Video Consultation with Patient" epic spec. FastAPI +
MongoDB signaling backend, a React Native (iOS/Android) mobile app, and a
browser-based web test client — 1:1 calls between a Command Centre user
("doctor" role today) and a patient.

## Structure

- `backend/` — FastAPI app: auth, roles/permissions, tenant scoping,
  appointments, WebSocket signaling for call setup (`app/signaling/ws_manager.py`),
  TURN/ICE credential issuance, call-session tokens, analytics events,
  alerting, and the REST endpoints in `app/routers/`.
- `mobile/` — React Native source (`src/context/CallContext.tsx` is the
  core call state machine; `src/screens/` for the call/incoming-call/
  post-call-notes UI; `src/services/` for signaling, WebRTC, and the pure
  decision-logic modules — network quality, video-quality adaptation,
  media-error mapping, audio-route selection).
- `web-test-client/` — a single-page browser client (`index.html` +
  a few standalone `.js` modules shared with the mobile logic where the
  decision is meant to match) for exercising the backend without a mobile
  build — auth, contact list, calling, and every in-call control.
- `scripts/` — automated verification: `verify_*.py` run end-to-end
  against a real backend (`_mock_server.py`, same FastAPI app with Mongo
  swapped for an in-memory mock), `test_*.js` cover pure/mocked
  client-side logic in Node. See `docs/RUN_LOCALLY.md` for exact commands.
- `docs/` — `BUILD_GUIDE.md` (native project generation, Play Store/App
  Store), `RUN_LOCALLY.md` (fastest path to running it, no MongoDB/mobile
  tooling needed), `TURN_SERVER_SETUP.md`, `DEMO_SCRIPT.md`, and a set of
  Word documents tracking scope: `Command_Centre_Epic_Gap_Analysis.docx`
  (the living section-by-section status against the epic — the source of
  truth for what's done/partial/not started), `Video_Calling_Status_Report.docx`
  (a shorter manager-facing summary), plus the original technical overview
  and edge-case risk register.

## What's implemented

Built out against the epic section by section; `docs/Command_Centre_Epic_Gap_Analysis.docx`
has the authoritative, up-to-date breakdown. As of the last round:

**Call flow & state** — invite → ringing → accept/decline → connect → end →
history, using the epic's own state/end-reason vocabulary. Ringing timeout,
patient decline, caller cancel, single-active-call locking (no double-booking
a user into two calls), and multi-device ringing (rings every logged-in
device, first accept wins) are all implemented and verified against a real
backend.

**Access control** — configurable role → permission mapping, plus tenant
scoping enforced on user listing, appointment creation, and `call:invite`.

**In-call features** — mute, camera on/off, front/rear camera flip,
voice ↔ video switching, a live network-quality indicator with graduated
outbound video-quality adaptation, camera/mic/speaker device pickers (web),
speaker/earpiece and Bluetooth audio routing (mobile, Bluetooth is
Android-only), and gated (off-by-default) automatic audio-only fallback on
sustained packet loss.

**Resilience** — reconnect-as-the-same-device (browser refresh or app
crash/restart resumes an in-progress call via a persisted `device_id`),
peer-disconnect/reconnect signaling with a grace period, and stale/replayed
signaling messages are ignored rather than acted on.

**Privacy & security** — patient consent required server-side before a call
connects, tenant boundaries enforced on every relevant query, and a
short-lived HMAC-signed call-session token (scoped to one call + user) with
a real consumer: `GET /calls/{call_id}/events` accepts it in place of a full
JWT.

**Post-call & audit** — notes/outcome capture on both web and mobile,
missed-call messaging, a full audit trail per call (timestamps, end reason,
platform, interruption/reconnection counts), structured analytics events,
and an alerting layer (unhandled exceptions and DB failures logged,
persisted, and optionally pushed to a webhook).

**Optional identity masking** — patients can be shown a generic "iLive Care
Team" label instead of the clinician's real name; off by default pending a
Business decision.

**Not implemented** (all need a real mobile device + native build toolchain,
not available in every dev environment): receiving calls while backgrounded/
locked (push notifications, iOS CallKit, Android Telecom), cellular-call
interruption handling, BLE vitals-monitor coexistence, and real-device
cross-platform QA. Error-message copy is not yet reconciled against the
epic's literal wording (pending that text). See Section 2 of the gap
analysis for the full picture and Section 7 for open decisions still
needing Business/Medical/Legal sign-off.

## Testing

Everything under `scripts/` is meant to be run, not just read — see
`docs/RUN_LOCALLY.md` for the exact setup and commands. In short:

```
pip install -r scripts\requirements-selfcheck.txt
python scripts\_mock_server.py          # in one terminal
python scripts\verify_video_call.py     # in another, and similarly for the other verify_*.py scripts
```

```
node scripts\test_network_quality.js
node scripts\test_media_errors.js
node scripts\test_device_picker.js
node scripts\test_audio_route.js
```

Current coverage: 326 automated checks across 15 suites (210 against the
real backend, 116 across 4 pure/mocked client-logic suites in Node) — see
`docs/Command_Centre_Epic_Gap_Analysis.docx` for the up-to-date count, since
this grows with every round of work — plus a clean mobile `tsc --noEmit`.

## Quick start

See `docs/RUN_LOCALLY.md` for the fastest path (Python + a browser, no
MongoDB or mobile build tooling required), or `docs/BUILD_GUIDE.md` for the
full walkthrough including native Android/iOS project generation and store
publishing.
