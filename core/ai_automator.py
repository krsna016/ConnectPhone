import logging
from PIL import ImageGrab
import numpy as np

class AIAutomator:
    """
    Hooks into YOLOv8 to visually understand the mirrored Android screen.
    Acts as the 'Ghost Driver' by identifying buttons, icons, and objects visually.
    Requires 'ultralytics' and 'opencv-python'.
    """
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        try:
            from ultralytics import YOLO
            # We use a lightweight 'nano' model for extreme real-time inference speed
            self.model = YOLO('yolov8n.pt') 
            print("[+] YOLOv8 AI Engine initialized.")
        except ImportError:
            self.logger.error("Ultralytics YOLO is not installed.")
            raise RuntimeError("Missing ML libraries. Run 'pip3 install ultralytics opencv-python'")

    def find_objects_on_screen(self, bbox=None):
        """
        Takes a snapshot of the screen and passes it to the Neural Network for analysis.
        Returns a list of detected objects and their exact mathematical coordinates on the screen.
        """
        print("[*] Taking visual snapshot for AI analysis...")
        screenshot = ImageGrab.grab(bbox=bbox)
        
        # Convert PIL Image to numpy array for PyTorch/OpenCV
        img_np = np.array(screenshot)
        
        # Run inference (This happens on the CPU/Neural Engine)
        results = self.model(img_np)
        
        detected = []
        for r in results:
            for box in r.boxes:
                label = self.model.names[int(box.cls[0])]
                confidence = float(box.conf[0])
                coords = box.xyxy[0].tolist() # [x1, y1, x2, y2]
                
                detected.append({
                    "label": label,
                    "confidence": confidence,
                    "coordinates": coords
                })
                print(f"[+] AI Found: {label} (Confidence: {confidence:.2f}) at {coords}")
                
        return detected

    def click_object(self, object_label: str, device_serial: str):
        """
        Uses the Neural Network to visually find an object, calculates its mathematical center,
        and injects an ADB click command at wire-speed using the Raw Socket Multiplexer.
        """
        from core.adb_multiplexer import AdbSocketMultiplexer
        
        print(f"[*] Searching for {object_label} to click...")
        objects = self.find_objects_on_screen()
        for obj in objects:
            if obj["label"].lower() == object_label.lower():
                coords = obj["coordinates"]
                center_x = int((coords[0] + coords[2]) / 2)
                center_y = int((coords[1] + coords[3]) / 2)
                print(f"[+] Found {object_label}! Firing microsecond ADB tap event to ({center_x}, {center_y})")
                
                # Wire-speed injection bypassing standard `adb` binary overhead
                multiplexer = AdbSocketMultiplexer()
                multiplexer.execute_instant_shell(device_serial, f"input tap {center_x} {center_y}")
                return True
                
        print(f"[-] AI could not find {object_label} on screen.")
        return False
