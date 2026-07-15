from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
import uvicorn
import logging
import asyncio
from core.auto_reconnect import AutoReconnector
from core.ocr_engine import OCREngine
from core.ai_automator import AIAutomator

app = FastAPI(title="ConnectPhone API", version="2.0")
logger = logging.getLogger(__name__)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
    serial = params.get("serial", "192.168.1.5:5555")
    
    if not offer or not type:
        return {"error": "Invalid SDP parameters"}
        
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
    # This will hook directly into your new _build_status_payload()
    return JSONResponse(content={"connected": True, "status": "FastAPI Active"})

@app.post("/api/action/ocr")
async def trigger_ocr():
    """Triggers the Tesseract OCR engine to read the screen and copy text."""
    try:
        ocr = OCREngine()
        extracted = ocr.extract_text_from_screen()
        return JSONResponse(content={"success": True, "text": extracted})
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

@app.post("/api/action/ai-click")
async def trigger_ai_click(request: Request):
    """Uses YOLOv8 to visually find an object and mathematically inject a raw ADB click."""
    try:
        data = await request.json()
        target_object = data.get("target")
        device_serial = data.get("serial", "192.168.1.5:5555") # Fallback to default if not provided
        
        if not target_object:
            return JSONResponse(content={"success": False, "error": "No target specified"}, status_code=400)
            
        ai = AIAutomator()
        found = ai.click_object(target_object, device_serial)
        
        return JSONResponse(content={"success": found, "target": target_object})
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    """
    Event-driven WebSocket endpoint.
    Completely eliminates the 1.2-second polling loop that hammered the CPU.
    """
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
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

if __name__ == "__main__":
    start_fastapi_server()
