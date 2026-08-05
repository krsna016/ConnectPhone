from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
import uvicorn
import logging
import asyncio
import secrets
import subprocess
from core.auto_reconnect import AutoReconnector

app = FastAPI(title="ConnectPhone API", version="2.0")
logger = logging.getLogger(__name__)
_api_token = ""
_status_provider = None


def set_api_token(token):
    global _api_token
    _api_token = token


def set_status_provider(provider):
    """Register the authoritative status builder owned by the UI server."""
    global _status_provider
    _status_provider = provider


def _connected_adb_serial(serial):
    if not isinstance(serial, str) or not serial or len(serial) > 256:
        return False
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(
        line.split()[:2] == [serial, "device"]
        for line in (result.stdout or "").splitlines()[1:]
    )


@app.middleware("http")
async def require_api_token(request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    
    token = request.headers.get("x-connectphone-token", "")
    if not token:
        # Fallback to query parameter token
        token = request.query_params.get("token", "")
        
    if not _api_token or not secrets.compare_digest(token, _api_token):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8282", "http://127.0.0.1:8282"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instance of the auto-reconnector daemon
auto_reconnector = AutoReconnector()

@app.on_event("startup")
async def startup_event():
    print("[*] FastAPI Server Booting...")
    # Start the network watcher daemon automatically!
    auto_reconnector.start_watching()

# Removed static mount to prevent PyInstaller path crash (UI served by legacy httpd)
# from fastapi.staticfiles import StaticFiles
# app.mount("/", StaticFiles(directory="ui", html=True), name="ui")

# Instantiate WebRTC Streamer
from core.webrtc_streamer import WebRTCCloudStreamer
webrtc_engine = WebRTCCloudStreamer()

@app.post("/api/webrtc/offer")
async def webrtc_offer(request: Request):
    """
    Cryptographic SDP Handshake endpoint for Cloud Streaming.
    Receives an SDP offer from the browser, binds it to the raw scrcpy VideoStreamTrack,
    and returns the generated Answer.
    """
    params = await request.json()
    offer = params.get("sdp")
    type = params.get("type")
    serial = params.get("serial", "")
    if not isinstance(offer, str) or len(offer) > 2_000_000 or type not in {"offer", "answer"}:
        return {"error": "Invalid SDP parameters"}
    if not isinstance(serial, str) or len(serial) > 256 or any(ch in serial for ch in "\r\n\x00"):
        return {"error": "Invalid device serial"}
        
    if not _connected_adb_serial(serial):
        return JSONResponse({"error": "The requested device is not connected"}, status_code=409)
    answer = await webrtc_engine.handle_offer(offer, type, serial)
    return answer

# ---------------------------------------------------------
# OCR AND AI ROUTES
# ---------------------------------------------------------

class WebSocketManager:
    """Manages real-time, event-driven connections to the browser."""
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except RuntimeError:
                pass

ws_manager = WebSocketManager()

@app.get("/api/status")
async def get_status():
    """
    Lightning-fast async status endpoint.
    Replaces the blocking `do_GET` /api/status from the old http.server.
    """
    if _status_provider is None:
        return JSONResponse(content={"connected": False, "status": "Status provider unavailable"}, status_code=503)
    return JSONResponse(content=_status_provider())

@app.post("/api/action/ocr")
async def trigger_ocr():
    return JSONResponse(content={"success": False, "error": "This feature has been retired from ConnectPhone."}, status_code=410)

@app.post("/api/action/ai-click")
async def trigger_ai_click(request: Request):
    return JSONResponse(content={"success": False, "error": "This feature has been retired from ConnectPhone."}, status_code=410)

@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    """
    Event-driven WebSocket endpoint.
    Completely eliminates the 1.2-second polling loop that hammered the CPU.
    """
    if not _api_token or not secrets.compare_digest(websocket.headers.get("x-connectphone-token", ""), _api_token):
        await websocket.close(code=1008)
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive indefinitely
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

def start_fastapi_server(port=8282):
    """Starts the ultra-fast Uvicorn ASGI server engine."""
    logger.info(f"Starting FastAPI Engine on port {port}...")
    # Uvicorn handles thousands of async connections natively
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

if __name__ == "__main__":
    start_fastapi_server()
