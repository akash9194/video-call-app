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
4. Sign up as two different people, one per tab — e.g. "Akash (Client)" in tab 1, "Freelancer Bob" in tab 2. Leave both signed in.

## Recording (start your screen recorder now)

1. **Show both tabs side by side.** Point out each shows the other as "Online" — that's real-time presence over a WebSocket, not a page refresh.
2. **In tab 1, click "Video"** next to the other person. Narrate: this sends a `call:invite` over the signaling connection.
3. **Switch to tab 2** — the incoming call screen appears with Accept/Decline. Click **Accept**.
4. **Both tabs now show live video** using your webcam — this is a real WebRTC peer-to-peer connection; camera/mic frames are going directly between the two tabs, not through the server.
5. **Click "Switch to voice"** in either tab. Narrate: the video track just gets disabled locally and the other side is notified — no renegotiation needed, which is why it's instant.
6. **Click "Switch to video"** again to turn it back on.
7. *(Optional, to show the harder case)* Start a **new call as Voice** instead of Video from the home screen, then switch to video mid-call — narrate that because this call started with no video track at all, turning video on triggers a real WebRTC renegotiation (a second offer/answer exchange) rather than just flipping a flag.
8. **Click End** in either tab — both return to the home screen.

## Optional: show the automated verification instead of / in addition to clicking through

```
python scripts\verify_video_call.py
```
This places the same kind of call programmatically against the real backend and prints pass/fail for each step — useful if you want to show leads there's an automated regression check, not just a manual demo.
