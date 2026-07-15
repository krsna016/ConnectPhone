import os
import sys
import time
import subprocess

# Morse Code Dictionary
MORSE_DICT = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
    'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
    'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
    'Y': '-.--', 'Z': '--..', '1': '.----', '2': '..---', '3': '...--',
    '4': '....-', '5': '.....', '6': '-....', '7': '--...', '8': '---..',
    '9': '----.', '0': '-----', ' ': ' '
}

def translate_to_morse(text):
    text = text.upper()
    morse_list = []
    for char in text:
        if char in MORSE_DICT:
            morse_list.append(MORSE_DICT[char])
        else:
            morse_list.append('')
    return ' '.join(morse_list)

def send_adb_tap(x, y):
    subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def toggle_quick_settings_flashlight(x, y, duration):
    # Tap to turn ON
    send_adb_tap(x, y)
    
    # Wait for the duration of the dot or dash
    time.sleep(duration)
    
    # Tap to turn OFF
    send_adb_tap(x, y)

def run_morse_signals(morse_code, x, y, unit_duration=0.4):
    print(f"\n⚡ Broadcasting Morse Code: {morse_code}")
    print("Press Ctrl+C to abort.")
    
    try:
        # Expand Quick Settings once at the beginning
        subprocess.run(["adb", "shell", "cmd", "statusbar", "expand-settings"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)  # Wait for panel to open
        
        # Loop through each character symbol
        for word in morse_code.split('   '):
            for letter in word.split(' '):
                for symbol in letter:
                    if symbol == '.':
                        print("•", end="", flush=True)
                        toggle_quick_settings_flashlight(x, y, unit_duration)
                    elif symbol == '-':
                        print("—", end="", flush=True)
                        toggle_quick_settings_flashlight(x, y, unit_duration * 3)
                    
                    # Gap between elements in the same letter
                    time.sleep(unit_duration)
                
                # Gap between letters
                print(" ", end="", flush=True)
                time.sleep(unit_duration * 2)
            
            # Gap between words
            print("   ", end="", flush=True)
            time.sleep(unit_duration * 4)
        print("\n\n✅ Transmission finished!")
    except KeyboardInterrupt:
        print("\n\n❌ Transmission aborted.")
    finally:
        # Ensure statusbar is collapsed once at the end
        subprocess.run(["adb", "shell", "cmd", "statusbar", "collapse"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    print("=========================================")
    # ASCII Art
    print("      Android Morse Flashlight Clicker    ")
    print("=========================================")
    
    # Verify device connection
    devices = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout.strip().split("\n")[1:]
    connected = [line for line in devices if line.strip()]
    if not connected:
        print("❌ Error: No Android devices connected via ADB. Please connect a device first.")
        sys.exit(1)
        
    print(f"✅ Connected to: {connected[0].split()[0]}")
    
    print("\nℹ️ How it works:")
    print("1. Enable 'Pointer location' in Developer Options to find the exact coordinates of your Flashlight Tile.")
    print("2. Expand your notification shade and locate the Flashlight Quick Settings Tile.")
    print("3. Enter the X and Y coordinates below.")
    
    # Default coordinates (commonly around center-left on many screens when statusbar is expanded)
    try:
        x = int(input("\nEnter Flashlight Tile X coordinate (default: 300): ").strip() or "300")
        y = int(input("Enter Flashlight Tile Y coordinate (default: 360): ").strip() or "360")
    except ValueError:
        print("❌ Invalid input. Please enter integers for coordinates.")
        sys.exit(1)
        
    unit_duration = float(input("\nEnter unit duration in seconds (default: 0.3s): ").strip() or "0.3")
    
    while True:
        try:
            text = input("\nEnter English text to send (or 'exit' to quit): ").strip()
            if not text or text.lower() == 'exit':
                break
                
            morse = translate_to_morse(text)
            print(f"Morse: {morse}")
            
            confirm = input("Press Enter to start flashing Morse Code...").strip()
            run_morse_signals(morse, x, y, unit_duration)
            
        except KeyboardInterrupt:
            break
            
    print("\nGoodbye!")

if __name__ == "__main__":
    main()
