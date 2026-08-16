# video-call-app

Video calling feature for a freelancing platform. React Native (iOS/Android) frontend, Python (FastAPI) backend, MongoDB, self-hosted WebRTC signaling — 1:1 calls.

## Structure

- `backend/` — FastAPI app: auth, user list, WebSocket signaling for call setup, ICE server config endpoint.
- `mobile/` — React Native source (`App.tsx`, `src/`) for login, contact list, incoming call screen, and the call screen itself.
- `docs/BUILD_GUIDE.md` — how to run this locally, generate the native Android/iOS projects, and publish to the Play Store / App Store.

## Quick start

See `docs/BUILD_GUIDE.md` — start there, it walks through backend setup, native project generation, and running on device/emulator.
