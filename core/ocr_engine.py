import pytesseract
from PIL import Image, ImageGrab
import logging
import subprocess
import os

class OCREngine:
    """
    Handles Optical Character Recognition (OCR) for extracting text from the mirrored screen.
    Demonstrates clean separation of AI/Image processing logic.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Configure Tesseract path for Apple Silicon (Homebrew)
        if os.path.exists('/opt/homebrew/bin/tesseract'):
            pytesseract.pytesseract.tesseract_cmd = '/opt/homebrew/bin/tesseract'
        elif os.path.exists('/usr/local/bin/tesseract'):
            pytesseract.pytesseract.tesseract_cmd = '/usr/local/bin/tesseract'
            
        self._check_tesseract_installed()

    def _check_tesseract_installed(self):
        """Verifies the engine is installed at the OS level before allowing execution."""
        try:
            version = pytesseract.get_tesseract_version()
            self.logger.info(f"Tesseract {version} initialized successfully.")
        except pytesseract.TesseractNotFoundError:
            self.logger.error("Tesseract is not installed.")
            raise RuntimeError("Tesseract OCR is missing. Run 'brew install tesseract' on macOS.")

    def extract_text_from_screen(self, bbox=None) -> str:
        """
        Captures a portion of the screen and extracts text via OCR.
        :param bbox: Tuple of (left, top, right, bottom). If None, captures whole screen.
        """
        try:
            print("[*] Taking snapshot of the screen for OCR...")
            screenshot = ImageGrab.grab(bbox=bbox)
            
            print("[*] Running Optical Character Recognition...")
            # Use Tesseract to mathematically extract text from the pixels
            extracted_text = pytesseract.image_to_string(screenshot).strip()
            
            if extracted_text:
                print(f"[+] OCR Success! Extracted {len(extracted_text)} characters.")
                self.copy_to_clipboard(extracted_text)
            else:
                print("[-] No text found in the image.")
            
            return extracted_text
            
        except Exception as e:
            self.logger.error(f"OCR Extraction failed: {e}")
            return ""

    def copy_to_clipboard(self, text: str):
        """Injects the extracted text directly into the macOS clipboard."""
        try:
            process = subprocess.Popen('pbcopy', env={'LANG': 'en_US.UTF-8'}, stdin=subprocess.PIPE)
            process.communicate(text.encode('utf-8'))
            print("[+] Text copied to your Mac clipboard! You can now paste it anywhere.")
        except Exception as e:
            self.logger.error(f"Failed to copy to clipboard: {e}")
