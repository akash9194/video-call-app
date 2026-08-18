# Running video-call-app locally (quick start)

The fastest path to seeing the app work: no MongoDB, no Docker, no mobile
build tooling — just Python and a browser. For the real-database version
or the actual mobile app, see the sections at the bottom.

## Prerequisites

- Python 3.10+ installed and on PATH
- Any modern browser

## 1. One-time setup

```
cd D:\iLiveConnect\video-call-app
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
pip install mongomock-motor
```

## 2. Start the backend

```
cd D:\iLiveConnect\video-call-app
venv\Scripts\activate
python scripts\_mock_server.py
```

Leave this terminal open — it's your running server, on `http://localhost:8123`,
with an in-memory database that resets every time you restart it.

**Port already in use?**
```
netstat -ano | findstr :8123
taskkill /PID <the number from that line> /F
```
This usually means a server from an earlier session is still running somewhere.

## 3. Serve the web test client (open a second terminal)

```
cd D:\iLiveConnect\video-call-app\web-test-client
python -m http.server 5500
```

## 4. Try it

1. Open `http://localhost:5500` in one browser tab.
2. Open it again in a second tab, or an incognito/private window (so it doesn't share login with the first).
3. In **both** tabs, change the "Backend URL" field near the top to `http://localhost:8123`.
4. Sign up a different account in each tab -- pick **Doctor** as the role in one, **Patient** in the other. Only a doctor account can start a call; this is enforced by the backend, not just the UI.
5. In the doctor's tab, click **Schedule** next to the patient and pick any date/time. A call can't be placed until an appointment exists between the two -- also enforced server-side.
6. Once each tab shows the other person as "Online," click Voice or Video **from the doctor's tab** to call the patient, and Accept in the patient's tab.

## Running the automated checks instead of clicking through by hand

```
pip install -r scripts\requirements-selfcheck.txt
python scripts\verify_video_call.py
```

Simulates a doctor and a patient placing a real WebRTC call (appointment included) and prints pass/fail for each step — should end with `RESULT: 16 passed, 0 failed`.

A few more scripts cover the other pieces -- run these against an already-running server (`python scripts\_mock_server.py` in one terminal, these in another):

```
python scripts\verify_renegotiation.py    # switching voice <-> video mid-call
python scripts\verify_multi_device.py     # same account signed in on 3 devices at once
python scripts\verify_doctor_patient.py   # doctor-only initiation + appointment enforcement, including rejection cases
python scripts\verify_consent.py          # patient consent is required before call:accept is honored
python scripts\verify_turn.py             # TURN relay -- skips cleanly until you've set TURN_URLS/TURN_SHARED_SECRET (see docs\TURN_SERVER_SETUP.md)
```

One more covers the ringing-timeout and reconnection/disconnect-grace behavior, which need short timeouts to test quickly rather than the real 45s/30s defaults:

```
set RINGING_TIMEOUT_SECONDS=3
set DISCONNECT_GRACE_SECONDS=3
python scripts\_mock_server.py
```
then, in another terminal, with the same two variables set:
```
set RINGING_TIMEOUT_SECONDS=3
set DISCONNECT_GRACE_SECONDS=3
python scripts\verify_call_resilience.py
```

## Running against real MongoDB (data persists between restarts)

Use this instead of step 2 above once you actually want persistence:

```
cd D:\iLiveConnect\video-call-app\backend
copy .env.example .env
```
Edit `.env` and set a real `JWT_SECRET_KEY`. Then start MongoDB (Docker Desktop must be running):
```
docker run -d -p 27017:27017 mongo
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Use `http://localhost:8000` as the Backend URL in the web client instead of `8123`.

## Running the actual mobile app (Android/iOS)

That needs Android Studio or Xcode and can't be done from a plain terminal or browser — see `docs\BUILD_GUIDE.md` for the full walkthrough.
