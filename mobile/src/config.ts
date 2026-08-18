// Point these at your backend. Use your machine's LAN IP (not "localhost")
// when testing on a physical device or emulator — see docs/BUILD_GUIDE.md.
// Pointed at the mock/test server (scripts/_mock_server.py), which is what's
// currently running and what the iPhone Safari test client is talking to via
// ngrok -- same port, same in-memory DB, so the doctor account signed up on
// the iPhone is visible here too. Switch to 8000 when running the real
// backend (uvicorn app.main:app) against real MongoDB instead.
export const API_BASE_URL = 'http://10.0.2.2:8123'; // 10.0.2.2 = Android emulator's alias for host localhost
export const WS_BASE_URL = 'ws://10.0.2.2:8123';
