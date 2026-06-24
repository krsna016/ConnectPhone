import os

def print_header(title):
    os.system('clear')
    print(f"{BLUE}{BOLD}=================================================={RESET}")
    print(f"{CYAN}{BOLD}📱 {title}{RESET}")
    print(f"{BLUE}{BOLD}=================================================={RESET}")

def run_mirroring_menu():
    while True:
        config = load_config()
        print_header("Mirroring & Camera Modes")
        print(f"1) {GREEN}🖥️ Standard Screen Mirroring{RESET}")
        print(f"2) {CYAN}📷 Live Camera Feed to Mac (With Mic Audio){RESET}")
        print(f"3) {CYAN}📷 Live Camera Feed to Mac (No Audio){RESET}")
        print(f"4) {BLUE}🔊 Audio-Only Mirroring (Listen to Phone Mic){RESET}")
        print(f"5) {MAGENTA}🎥 Native Phone Camera HD Record & Auto-Pull{RESET} (Full camera control, sync to Mac)")
        print(f"6) {MAGENTA}🎥 Mirror & Record Video Feed to Mac Desktop{RESET} (scrcpy feed record)")
        print(f"7) {YELLOW}⚙️ Edit Mirroring Preferences & Audio Profile{RESET}")
        print(f"8) {RED}🔙 Return to Main Menu{RESET}")
        
        choice = input(f"\nEnter choice (1-8): ").strip()
        if choice == "1":
            run_mirroring_flow(3, config)
        elif choice == "2":
            run_mirroring_flow(1, config)
        elif choice == "3":
            run_mirroring_flow(4, config)
        elif choice == "4":
            run_mirroring_flow(2, config)
        elif choice == "5":
            record_native_video_workflow()
        elif choice == "6":
            mirror_and_record(config)
        elif choice == "7":
            configure_preferences()
        elif choice == "8":
            break

def run_files_menu():
    while True:
        print_header("File Transfer & App Installer")
        print(f"1) {GREEN}📤 Push file from Mac to Phone's Download folder{RESET}")
        print(f"2) {CYAN}📥 Pull latest photo from Phone to Mac Downloads{RESET}")
        print(f"3) {BLUE}🔄 Start Auto-File Sync Monitor (Real-time sync to Desktop){RESET}")
        print(f"4) {MAGENTA}🔌 Install Android APK on Phone{RESET}")
        print(f"5) {RED}🔙 Return to Main Menu{RESET}")
        
        choice = input(f"\nEnter choice (1-5): ").strip()
        if choice == "1":
            push_file_to_phone()
        elif choice == "2":
            pull_latest_photos()
        elif choice == "3":
            watch_send_to_mac_folder()
        elif choice == "4":
            install_apk()
        elif choice == "5":
            break

def run_controls_menu():
    while True:
        config = load_config()
        print_header("Quick Controls & Input")
        print(f"1) {GREEN}🔑 Unlock via Mac Touch ID (Biometric Bridge){RESET}")
        print(f"2) {YELLOW}⚙️ Configure Android Backup PIN{RESET}")
        print(f"3) {YELLOW}⚙️ Configure App Lock PIN{RESET}")
        print(f"4) {GREEN}🔌 Simulate Power Button (Lock/Unlock){RESET}")
        print(f"5) {CYAN}🔊 Volume Up{RESET}")
        print(f"6) {CYAN}🔉 Volume Down{RESET}")
        print(f"7) {BLUE}🏠 Home Button{RESET}")
        print(f"8) {BLUE}🔙 Back Button{RESET}")
        print(f"9) {BLUE}🔀 App Switcher (Recent Apps){RESET}")
        print(f"10) {CYAN}🔇 Mute/Unmute Audio{RESET}")
        print(f"11) {CYAN}⏯️ Play/Pause Media{RESET}")
        print(f"12) {BLUE}⚙️ Open Android Settings App{RESET}")
        print(f"13) {MAGENTA}⌨️ Type text onto phone screen{RESET}")
        print(f"14) {YELLOW}📋 Show scrcpy Shortcut Cheat-sheet{RESET}")
        print(f"15) {RED}🔙 Return to Main Menu{RESET}")
        
        choice = input(f"\nEnter choice (1-15): ").strip()
        if choice == "1":
            unlock_device_with_touch_id(config)
        elif choice == "2":
            print_header("Configure Android PIN")
            pin = input("Enter your phone's unlock PIN (saved locally): ").strip()
            if not pin.isdigit() or len(pin) < 4:
                print(f"{RED}❌ Invalid PIN. Must be digits only and at least 4 characters.{RESET}")
            else:
                config["android_pin"] = pin
                save_config(config)
                print(f"{GREEN}✅ PIN saved successfully.{RESET}")
            input("\nPress Enter to continue...")
        elif choice == "3":
            print_header("Configure App Lock PIN")
            pin = input("Enter your App Lock PIN (saved locally): ").strip()
            if not pin.isdigit() or len(pin) < 4:
                print(f"{RED}❌ Invalid PIN. Must be digits only and at least 4 characters.{RESET}")
            else:
                config["applock_pin"] = pin
                save_config(config)
                print(f"{GREEN}✅ App Lock PIN saved successfully.{RESET}")
            input("\nPress Enter to continue...")
        elif choice == "4":
            subprocess.run(["adb", "shell", "input", "keyevent", "26"])
            print("Sent KEYCODE_POWER")
            input("\nPress Enter to continue...")
        elif choice == "5":
            subprocess.run(["adb", "shell", "input", "keyevent", "24"])
            print("Sent KEYCODE_VOLUME_UP")
            input("\nPress Enter to continue...")
        elif choice == "6":
            subprocess.run(["adb", "shell", "input", "keyevent", "25"])
            print("Sent KEYCODE_VOLUME_DOWN")
            input("\nPress Enter to continue...")
        elif choice == "7":
            subprocess.run(["adb", "shell", "input", "keyevent", "3"])
            print("Sent KEYCODE_HOME")
            input("\nPress Enter to continue...")
        elif choice == "8":
            subprocess.run(["adb", "shell", "input", "keyevent", "4"])
            print("Sent KEYCODE_BACK")
            input("\nPress Enter to continue...")
        elif choice == "9":
            subprocess.run(["adb", "shell", "input", "keyevent", "187"])
            print("Sent KEYCODE_APP_SWITCH")
            input("\nPress Enter to continue...")
        elif choice == "10":
            subprocess.run(["adb", "shell", "input", "keyevent", "164"])
            print("Sent KEYCODE_VOLUME_MUTE")
            input("\nPress Enter to continue...")
        elif choice == "11":
            subprocess.run(["adb", "shell", "input", "keyevent", "85"])
            print("Sent KEYCODE_MEDIA_PLAY_PAUSE")
            input("\nPress Enter to continue...")
        elif choice == "12":
            subprocess.run(["adb", "shell", "am", "start", "-a", "android.settings.SETTINGS"])
            print("Opened Android Settings")
            input("\nPress Enter to continue...")
        elif choice == "13":
            type_text()
        elif choice == "14":
            show_shortcuts()
        elif choice == "15":
            break

