import asyncio
import logging
import subprocess
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from av import VideoFrame
import time

class ScrcpyVideoStreamTrack(VideoStreamTrack):
    """
    A WebRTC VideoStreamTrack that reads raw H.264 frames from a headless scrcpy subprocess.
    """
    def __init__(self, device_serial: str):
        super().__init__()
        self.device_serial = device_serial
        # We boot scrcpy headless, asking for raw H264 on stdout, 800x600 for speed
        self.process = subprocess.Popen([
            "scrcpy",
            "-s", self.device_serial,
            "--no-display",
            "--video-codec=h264",
            "--max-size=800",
            "--max-fps=30",
            "--record-format=h264",
            "-" # dash means dump raw h264 to stdout
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        
        self.start_time = time.time()
        self.frames = 0
        
        # PyAV decoder
        import av
        self.codec = av.CodecContext.create('h264', 'r')

    async def recv(self):
        """
        Called by aiortc precisely when the browser wants the next frame.
        We read from scrcpy, decode it, and blast it to WebRTC.
        """
        pts, time_base = await self.next_timestamp()

        # In a production app, we would parse NAL units properly.
        # For this PoC, we grab chunks of stdout, feed PyAV, and yield frames.
        # This is a simplified wrapper for demonstration.
        try:
            chunk = self.process.stdout.read(4096)
            if not chunk:
                return None
            
            # Feed packet to decoder
            packets = self.codec.parse(chunk)
            for packet in packets:
                frames = self.codec.decode(packet)
                if frames:
                    frame = frames[0]
                    frame.pts = pts
                    frame.time_base = time_base
                    self.frames += 1
                    return frame
        except Exception as e:
            logging.error(f"WebRTC Frame Error: {e}")
            
        # Return a dummy frame to keep WebRTC alive if parsing fails
        import numpy as np
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        frame = VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame


class WebRTCCloudStreamer:
    """Enterprise WebRTC Engine."""
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.pcs = set()
        self.relay = MediaRelay()

    async def handle_offer(self, sdp: str, type: str, serial: str) -> dict:
        """Processes the SDP offer from Chrome/Safari and generates an Answer."""
        offer = RTCSessionDescription(sdp=sdp, type=type)
        pc = RTCPeerConnection()
        self.pcs.add(pc)

        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            if pc.iceConnectionState == "failed" or pc.iceConnectionState == "closed":
                await pc.close()
                self.pcs.discard(pc)

        # Attach the headless scrcpy track!
        track = ScrcpyVideoStreamTrack(serial)
        pc.addTrack(self.relay.subscribe(track))

        # Generate cryptographic answer
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        }
