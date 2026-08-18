# Demo script — voice/video calling feature

For a live recording with a screen recorder (Loom, OBS, Windows Game Bar). Two
browser tabs (or a normal tab + incognito window) side by side so leads can
see both sides of the call at once.

## Setup (do before you start recording)

1. Terminal 1:
   ```
   cd D:\iLiveConnect\video-call-app
   venv\Scripts\activate
   python scripts\_mock_server.py
   ```
2. Terminal 2:
   ```
   cd D:\iLiveConnect\video-call-app\web-test-client
   python -m http.server 5500
   ```
3. Open `http://localhost:5500` in two tabs/windows. In **both**, set the "Backend URL" field to `http://localhost:8123`.
4. Sign up as two different people, one per tab — pick **Doctor** as the role in tab 1 (e.g. "Dr. Akash"), and **Patient** in tab 2 (e.g. "Patient Bob"). Leave both signed in.

   This matters: only a doctor account can start a call. Signing both tabs up as patients (or trying to call from the patient tab) will get a rejected-call error instead of a ringing call — that's the doctor-only-initiation rule enforced server-side, not a bug.

5. **In the doctor's tab, click "Schedule"** next to the patient's name, pick any date/time, and confirm. This creates an appointment — a call can't be placed until one exists (also enforced server-side, not just hidden UI).

## Recording (start your screen recorder now)

1. **Show both tabs side by side.** Point out each shows the other as "Online" — that's real-time presence over a WebSocket, not a page refresh.
2. **In the doctor's tab, click "Video"** next to the patient. Narrate: this sends a `call:invite` over the signaling connection — and would be rejected server-side if this were the patient's tab instead, or if no appointment existed.
3. **Switch to the patient's tab** — the incoming call screen appears with Accept/Decline. Narrate: before Accept is even clickable, the patient has to check the consent box ("I consent to this telehealth video/voice consultation...") — this is also enforced server-side (the accept is rejected with `consent_required` if that flag isn't sent), not just a disabled button. Check it, then click **Accept**.
4. **Both tabs now show live video** using your webcam — this is a real WebRTC peer-to-peer connection; camera/mic frames are going directly between the two tabs, not through the server.
5. **Click "Switch to voice"** in either tab. Narrate: the video track just gets disabled locally and the other side is notified — no renegotiation needed, which is why it's instant.
6. **Click "Switch to video"** again to turn it back on.
7. *(Optional, to show the harder case)* Start a **new call as Voice** instead of Video from the doctor's tab, then switch to video mid-call — narrate that because this call started with no video track at all, turning video on triggers a real WebRTC renegotiation (a second offer/answer exchange) rather than just flipping a flag.
8. **Click End** in either tab — both return to the home screen.
9. *(Optional, to show the permission model)* Try clicking "Video" from the **patient's** tab (there's no button — narrate that it's not just hidden, the backend would reject it even if you called the API directly), or have the doctor call a *different* patient with no appointment yet, and show the "Call not started" error.

## Optional: show the automated verification instead of / in addition to clicking through

```
python scripts\verify_video_call.py          # full call, 16 checks
python scripts\verify_renegotiation.py       # voice<->video mid-call switch (needs a server already running -- see below)
python scripts\verify_multi_device.py        # same account on 3 devices at once
python scripts\verify_doctor_patient.py      # doctor-only initiation + appointment enforcement
python scripts\verify_turn.py                # TURN relay (skips cleanly if you haven't set up TURN yet)
```
`verify_video_call.py` starts and stops its own server. The other four expect one already running (`python scripts\_mock_server.py` in another terminal first) since they're meant to be run together during a dev session. Each places real calls against the real backend and prints pass/fail per step — useful if you want to show leads there's an automated regression suite, not just a manual demo.
