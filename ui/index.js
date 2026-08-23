document.addEventListener('DOMContentLoaded', () => {
    // API base URL (detect file:// protocol to point to local Python server)
    const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8282' : '';
    // Keep the token in the URL fragment so it is not sent to the local HTTP
    // server in request logs or included in Referer headers. Cache it in
    // sessionStorage so the capability disappears when the app view closes.
    let hashToken = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('token');
    if (hashToken) {
        sessionStorage.setItem('cp_api_token', hashToken);
        // Clear hash to prevent leaking the token in history or sharing
        window.location.hash = '';
    }
    const API_TOKEN = sessionStorage.getItem('cp_api_token') || '';

    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
        const headers = new Headers(init.headers || {});
        if (API_TOKEN) headers.set('X-ConnectPhone-Token', API_TOKEN);
        return nativeFetch(input, { ...init, headers });
    };

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }
    
    // State variables
    let currentTab = 'connection';
    let statusInterval = null;
    let scrcpyWasRunning = false;
    let isRecording = false;
    let _actionInFlight = false;  // pause polling during long operations
    let dependencyWarningShown = false;

    // Storage Manager State
    window.currentStoragePath = '/sdcard';
    window.isConnected = false;

    // DOM Elements
    const navButtons = document.querySelectorAll('.nav-btn');
    const storageDisconnectedAlert = document.getElementById('storage-disconnected-alert');
    const storageMainView = document.getElementById('storage-main-view');
    const storageCurrentPath = document.getElementById('storage-current-path');
    const tabPanes = document.querySelectorAll('.tab-pane');
    const connStatus = document.getElementById('connection-status');
    const connStatusText = connStatus.querySelector('.status-text');
    const headerDevice = document.getElementById('header-device-info');
    const btnRefresh = document.getElementById('btn-refresh');
    const toastContainer = document.getElementById('toast-container');
    const btnHeaderUnlock = document.getElementById('btn-header-unlock');
    const btnPhoneUnlock = document.getElementById('btn-phone-unlock');
    
    // Camera Control Elements
    const cameraOverlay = document.getElementById('camera-overlay');
    const cameraOverlayTitle = document.getElementById('camera-overlay-title');
    const cameraOverlayDesc = document.getElementById('camera-overlay-desc');
    const overlayCapture = document.getElementById('overlay-capture');
    const overlayRecord = document.getElementById('overlay-record');
    const overlayStop = document.getElementById('overlay-stop');

    // Dropdown change listeners
    const savedIpsDropdown = document.getElementById('saved-ips-dropdown');
    if (savedIpsDropdown) {
        savedIpsDropdown.addEventListener('change', (e) => {
            if (e.target.value) {
                try { selectSavedDevice(JSON.parse(e.target.value)); } catch (_) {
                    document.getElementById('conn-ip').value = e.target.value;
                }
            }
        });
    }
    
    const modalSavedIpsDropdown = document.getElementById('modal-saved-ips-dropdown');
    if (modalSavedIpsDropdown) {
        modalSavedIpsDropdown.addEventListener('change', (e) => {
            if (e.target.value) {
                try { selectSavedDevice(JSON.parse(e.target.value), true); } catch (_) {
                    document.getElementById('modal-ip-input').value = e.target.value;
                }
            }
        });
    }

    function selectSavedDevice(device, modal = false) {
        const ipInput = document.getElementById(modal ? 'modal-ip-input' : 'conn-ip');
        const portInput = document.getElementById('conn-port');
        if (ipInput) ipInput.value = device.ip || '';
        if (!modal && portInput && device.port) portInput.value = device.port;
    }

    // Sidebar navigation switching
    navButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.getAttribute('data-tab');
            switchTab(tabName);
        });
    });

    function switchTab(tabName) {
        currentTab = tabName;
        navButtons.forEach(b => {
            if (b.getAttribute('data-tab') === tabName) {
                b.classList.add('active');
            } else {
                b.classList.remove('active');
            }
        });

        tabPanes.forEach(pane => {
            if (pane.id === `pane-${tabName}`) {
                pane.classList.add('active');
            } else {
                pane.classList.remove('active');
            }
        });

        // Toggle Live Metrics polling
        if (tabName === 'metrics') {
            startMetricsPolling();
        } else {
            stopMetricsPolling();
        }

        if (tabName === 'storage') {
            if (window.loadStorageDirectory) window.loadStorageDirectory(window.currentStoragePath);
        }


    }
    // Refresh devices list manually
    btnRefresh.addEventListener('click', () => {
        showToast('Scanning for connected devices...', 'info');
        fetchStatus(true);
    });

    // Toast Notifications
    function showToast(message, type = 'success') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        
        let icon = '<i class="material-symbols-outlined">notifications</i>';
        if (type === 'success') icon = '<i class="material-symbols-outlined">check_circle</i>';
        else if (type === 'error') icon = '<i class="material-symbols-outlined">cancel</i>';
        else if (type === 'info') icon = '<i class="material-symbols-outlined">hourglass_empty</i>';
        
        toast.innerHTML = `
            <span class="toast-icon">${icon}</span>
            <span class="toast-message">${escapeHtml(message)}</span>
        `;
        
        toastContainer.appendChild(toast);
        
        // Remove toast after 4.5 seconds
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateY(20px)';
            setTimeout(() => {
                toast.remove();
            }, 300);
        }, 4500);
    }

    // Fetch Status and update UI
    async function fetchStatus(isManual = false) {
        if (_actionInFlight && !isManual) return;
        try {
            const res = await fetch(`${API_BASE}/api/status`);
            if (!res.ok) throw new Error("HTTP connection error");
            const data = await res.json();
            updateConnectionUI(data);
            updatePreferencesForm(data.config);
            updateCameraOverlayUI(data);
            if (isManual) {
                if (data.connected) {
                    showToast('Devices scanned successfully. Phone is connected.', 'success');
                } else {
                    showToast('No active devices found. Pair or connect wirelessly.', 'error');
                }
            }
            return data;
        } catch (err) {
            console.error("Failed to query API status:", err);
            if (connStatus) connStatus.className = 'connection-badge server-offline';
            if (connStatusText) connStatusText.textContent = 'Server Offline';
            if (headerDevice) headerDevice.textContent = 'Cannot reach Python API server. Please run ConnectPhoneUI.app or start ConnectPhoneUI.py in terminal.';
            return null;
        }
    }

    function updateConnectionUI(data) {
        const missingDependencies = Object.entries(data.dependencies || {}).filter(([, available]) => !available).map(([name]) => name);
        if (missingDependencies.length && !dependencyWarningShown) {
            dependencyWarningShown = true;
            showToast(`Missing required tools: ${missingDependencies.join(', ')}. Install Android Platform Tools, scrcpy, and FFmpeg.`, 'error');
        }
        const cleanInfo = (data.device_info || "").replace(/\\033\[[0-9;]*m/g, '').replace(/\x1b\[[0-9;]*m/g, '');
        
        const wasConnected = window.isConnected;
        window.isConnected = !!data.connected;
        window.activeDevice = data.connected ? (data.active_device || '') : '';
        syncMdnsConnectionButtons();
        if (storageDisconnectedAlert && storageMainView) {
            if (window.isConnected) {
                storageDisconnectedAlert.style.display = 'none';
                storageMainView.style.display = 'flex';
                if (!wasConnected) loadPhoneStorages();
                
                // If we just connected, or if we are on the storage tab and it hasn't loaded yet (e.g. empty path input)
                if ((!wasConnected || !storageCurrentPath.value) && currentTab === 'storage') {
                    if (window.loadStorageDirectory) window.loadStorageDirectory(window.currentStoragePath);
                }
            } else {
                storageDisconnectedAlert.style.display = 'flex';
                storageMainView.style.display = 'none';
            }
        }

        if (data.connected) {
            connStatus.className = 'connection-badge connected';
            connStatusText.textContent = 'Connected';
            
            // Header display
            const isWireless = data.active_device && data.active_device.includes(':');
            if (isWireless) {
                headerDevice.innerHTML = `<span style="font-size:13px; color: var(--text-secondary);"><i class="material-symbols-outlined" style="font-size:13px">wifi</i> Connected over Wi-Fi</span>`;
            } else {
                headerDevice.innerHTML = `<span style="font-size:13px; color: var(--text-secondary);"><i class="material-symbols-outlined" style="font-size:13px">cable</i> Connected via USB</span>`;
            }
            if (btnHeaderUnlock) btnHeaderUnlock.style.display = 'inline-flex';
            if (btnPhoneUnlock) btnPhoneUnlock.disabled = false;
        } else {
            connStatus.className = 'connection-badge disconnected';
            connStatusText.textContent = 'Disconnected';
            headerDevice.innerHTML = 'Connection Center <i class="material-symbols-outlined">link</i> Connect using USB or Wi-Fi IP';
            if (btnHeaderUnlock) btnHeaderUnlock.style.display = 'none';
            if (btnPhoneUnlock) btnPhoneUnlock.disabled = true;
        }

        // Toggle input event injection security warning box
        const secWarning = document.getElementById('security-settings-warning');
        if (secWarning) {
            if (data.connected && data.input_injection_granted === false) {
                secWarning.classList.remove('hidden');
            } else {
                secWarning.classList.add('hidden');
            }
        }

        // Populate ADB devices list in Connection Center
        const adbList = document.getElementById('adb-devices-list');
        const pulseIndicator = document.getElementById('device-pulse-indicator');
        const activeDetailsBox = document.getElementById('active-device-details-box');
        
        if (adbList) {
            adbList.innerHTML = '';
            
            const devices = data.devices_detailed || [];
            if (devices.length === 0) {
                adbList.innerHTML = '<p class="list-placeholder">No attached ADB devices found. Plug in via USB or connect over Wi-Fi.</p>';
                if (pulseIndicator) pulseIndicator.className = 'pulse-indicator disconnected';
                if (activeDetailsBox) {
                    activeDetailsBox.innerHTML = '<p class="status-placeholder">No active Android device is currently selected. Connect a device to begin.</p>';
                }
            } else {
                // Determine status for pulse indicator
                let hasOnline = devices.some(d => d.status === 'device');
                let hasUnauthorized = devices.some(d => d.status === 'unauthorized');
                
                if (pulseIndicator) {
                    if (hasOnline) pulseIndicator.className = 'pulse-indicator connected';
                    else if (hasUnauthorized) pulseIndicator.className = 'pulse-indicator unauthorized';
                    else pulseIndicator.className = 'pulse-indicator disconnected';
                }
                
                // Active details box rendering
                if (activeDetailsBox) {
                    if (data.connected && data.device_info) {
                        // Parse info: Device: model | Battery: level | Storage: storage_info
                        const match = cleanInfo.match(/Device:\s*(.*?)\s*\|\s*Battery:\s*(.*?)\s*\|\s*Storage:\s*(.*)/i);
                        if (match) {
                            const model = match[1];
                            const battery = match[2];
                            const storage = match[3];
                            
                                    let displayActiveSerial = data.devices[0] || 'USB Connection';
                                    if (displayActiveSerial.includes('._adb-tls-connect._tcp')) {
                                        displayActiveSerial = displayActiveSerial.replace('._adb-tls-connect._tcp', '');
                                        if (displayActiveSerial.startsWith('adb-')) displayActiveSerial = displayActiveSerial.substring(4);
                                        displayActiveSerial = 'ID: ' + displayActiveSerial;
                                    }
                                    
                                    activeDetailsBox.innerHTML = `
                                        <div class="active-device-details">
                                            <div class="detail-item">
                                                <span><i class="material-symbols-outlined">smartphone</i> Device Model</span>
                            <p>${escapeHtml(model)}</p>
                                            </div>
                                            <div class="detail-item">
                                                <span><i class="material-symbols-outlined">battery_full</i> Battery Level</span>
                            <p>${escapeHtml(battery)}</p>
                                            </div>
                                            <div class="detail-item">
                                                <span><i class="material-symbols-outlined">save</i> Available Storage</span>
                            <p>${escapeHtml(storage)}</p>
                                            </div>
                                            <div class="detail-item">
                                                <span><i class="material-symbols-outlined">public</i> IP Address / Serial</span>
                            <p>${escapeHtml(displayActiveSerial)}</p>
                                            </div>
                                        </div>
                                    `;
                        } else {
                            activeDetailsBox.innerHTML = `<p class="status-placeholder">${escapeHtml(cleanInfo)}</p>`;
                        }
                    } else {
                        activeDetailsBox.innerHTML = '<p class="status-placeholder">Device attached but offline or unauthorized. Please verify the debugging prompt on your phone screen.</p>';
                    }
                }
                
                // Add rows to the devices list
                devices.forEach(device => {
                    const row = document.createElement('div');
                    const isActive = (device.serial === data.active_device);
                    row.className = `device-row ${isActive ? 'active-device' : ''}`;
                    
                    row.addEventListener('click', async () => {
                        try {
                            const res = await postAction('/api/devices/select', { serial: device.serial });
                            if (res && res.success) {
                                showToast(res.message, 'success');
                                fetchStatus();
                            } else {
                                showToast(res ? res.message : 'Failed to select device', 'error');
                            }
                        } catch (err) {
                            showToast(`Error: ${err.message}`, 'error');
                        }
                    });
                    
                    const isWireless = device.type === 'wireless';
                    const icon = isWireless ? '<i class="material-symbols-outlined">wifi</i>' : '<i class="material-symbols-outlined">cable</i>';
                    const statusText = device.status === 'device' ? 'online' : (device.status === 'unauthorized' ? 'unauthorized' : 'offline');
                    
                    let displaySerial = device.serial;
                    if (displaySerial.includes('._adb-tls-connect._tcp')) {
                        displaySerial = displaySerial.replace('._adb-tls-connect._tcp', '');
                        if (displaySerial.startsWith('adb-')) displaySerial = displaySerial.substring(4);
                        displaySerial = 'ID: ' + displaySerial;
                    }
                    
                    row.innerHTML = `
                        <div class="device-info-left">
                            <span class="device-type-icon">${icon}</span>
                            <div class="device-meta">
                            <h4>${escapeHtml(device.model)}</h4>
                            <p>${escapeHtml(displaySerial)} (${isWireless ? 'Wi-Fi' : 'USB'})</p>
                            </div>
                        </div>
                        <div class="device-info-right">
                            <span class="status-badge ${statusText}">${statusText}</span>
                            ${isWireless ? `<button class="btn btn-sm btn-danger btn-device-disconnect" data-serial="${escapeHtml(device.serial)}">Disconnect</button>` : ''}
                        </div>
                    `;
                    
                    // Bind disconnect button
                    const discBtn = row.querySelector('.btn-device-disconnect');
                    if (discBtn) {
                        discBtn.addEventListener('click', async (e) => {
                            e.stopPropagation();
                            showToast(`Disconnecting ${device.serial}...`, 'info');
                            try {
                                const parts = device.serial.split(':');
                                const res = await fetch(`${API_BASE}/api/disconnect`, {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ ip: parts[0], port: parts[1] })
                                });
                                const resData = await res.json();
                                showToast(resData.message || 'Disconnected.', resData.success ? 'success' : 'error');
                                fetchStatus();
                            } catch (err) {
                                showToast(`Error: ${err.message}`, 'error');
                            }
                        });
                    }
                    
                    adbList.appendChild(row);
                });
            }
        }
    }

    function updateCameraOverlayUI(data) {
        const isRunning = data.scrcpy_running;
        isRecording = data.recording_active;
        const type = data.mirror_type || 'screen';

        // Automatically toggle overlay if scrcpy runs
        if (isRunning) {
            cameraOverlay.classList.remove('hidden');
            
            // Adjust overlay depending on mirror type
            if (type === 'camera') {
                // Show capture & record options
                if (overlayCapture) overlayCapture.style.display = 'inline-flex';
                if (overlayRecord) overlayRecord.style.display = 'inline-flex';
                
                if (cameraOverlayTitle) cameraOverlayTitle.textContent = 'Live Camera Feed';
                if (overlayStop) overlayStop.title = 'Stop camera stream';
                
                if (isRecording) {
                    overlayRecord.classList.add('recording');
                    overlayRecord.innerHTML = '<i class="material-symbols-outlined">circle</i>';
                    cameraOverlayDesc.textContent = 'RECORDING LIVE HD VIDEO (Saving to Desktop)...';
                } else {
                    overlayRecord.classList.remove('recording');
                    overlayRecord.innerHTML = '<i class="material-symbols-outlined">videocam</i>';
                    cameraOverlayDesc.textContent = 'Camera stream is active. Snap photo or record HD clip.';
                }
            } else {
                // Hide capture & record options for non-camera sessions
                if (overlayCapture) overlayCapture.style.display = 'none';
                if (overlayRecord) overlayRecord.style.display = 'none';
                
                if (overlayStop) overlayStop.title = 'Stop session';
                
                if (type === 'audio') {
                    if (cameraOverlayTitle) cameraOverlayTitle.textContent = 'Active Microphone Stream';
                    cameraOverlayDesc.textContent = 'Streaming device audio feed natively.';
                } else if (type === 'record') {
                    if (cameraOverlayTitle) cameraOverlayTitle.textContent = 'Recording Screen Session';
                    cameraOverlayDesc.textContent = 'Session is being mirrored and recorded to Desktop.';
                } else {
                    if (cameraOverlayTitle) cameraOverlayTitle.textContent = 'Active Screen Mirror';
                    cameraOverlayDesc.textContent = 'Device screen is being mirrored onto your Mac.';
                }
            }
            
            if (!scrcpyWasRunning) {
                const toastMsg = type === 'camera' ? 'Camera stream active! Controls are now available.' : 'Mirroring session launched successfully!';
                showToast(toastMsg, 'success');
            }
        } else {
            cameraOverlay.classList.add('hidden');
            if (overlayRecord) {
                overlayRecord.classList.remove('recording');
                overlayRecord.innerHTML = '<i class="material-symbols-outlined">videocam</i>';
            }
            
            if (scrcpyWasRunning) {
                showToast('Mirroring session closed.', 'info');
            }
        }
        
        scrcpyWasRunning = isRunning;
    }



    // Set preference inputs from saved configuration json
    let preferencesLoaded = false;
    function updatePreferencesForm(config) {
        if (!config) return;

        if (!preferencesLoaded) {
            preferencesLoaded = true;

            document.getElementById('pref-codec').value = config.camera_codec || 'h265';
            document.getElementById('pref-bitrate').value = config.camera_bitrate || '32M';
            document.getElementById('pref-fps').value = config.camera_fps || '60';
            document.getElementById('pref-audio-preset').value = config.audio_preset || 'voice_communication';
            document.getElementById('pref-sync-delay').value = config.audio_sync_delay || '0.80';
            document.getElementById('pref-keyboard').value = config.keyboard_mode || 'uhid';
            document.getElementById('pref-pin').value = config.android_pin || '';
            document.getElementById('pref-applock').value = config.applock_pin || '';
        
            const micSelect = document.getElementById('pref-mac-mic-device');
            if (micSelect) {
                micSelect.value = config.mac_mic_device || 'default';
            }
        

        
            document.getElementById('pref-audio-buffer').value = config.audio_buffer || '20';
        
            document.getElementById('pref-mirror-enabled').checked = config.mirror_enabled !== false;
            document.getElementById('pref-screen-off').checked = config.screen_off_enabled === true;
            document.getElementById('pref-stay-awake').checked = config.stay_awake_enabled !== false;
            document.getElementById('pref-show-touches').checked = config.show_touches_enabled === true;
            document.getElementById('pref-biometric-daemon').checked = config.biometric_daemon_enabled === true;
        }

        // Auto fill IP in connection form
        const connIpInput = document.getElementById('conn-ip');
        if (config.last_ip && connIpInput && !connIpInput.value) {
            connIpInput.value = config.last_ip;
        }
        const connPortInput = document.getElementById('conn-port');
        if (config.last_port && connPortInput && !connPortInput.value) {
            connPortInput.value = config.last_port;
        }
        
        // Populate saved IPs dropdowns
        const dropdown1 = document.getElementById('saved-ips-dropdown');
        const dropdown2 = document.getElementById('modal-saved-ips-dropdown');
        const savedDevices = Array.isArray(config.saved_devices) && config.saved_devices.length
            ? config.saved_devices
            : (config.saved_ips || []).map(ip => ({ ip, port: config.last_port || null }));
        
        [dropdown1, dropdown2].forEach(dropdown => {
            if (dropdown) {
                dropdown.innerHTML = '<option value="" disabled selected>▼</option>';
                savedDevices.forEach(device => {
                    const option = document.createElement('option');
                    option.value = JSON.stringify({ ip: device.ip, port: device.port });
                    option.textContent = device.port ? `${device.ip}:${device.port}` : device.ip;
                    dropdown.appendChild(option);
                });
                dropdown.onchange = () => {
                    try { selectSavedDevice(JSON.parse(dropdown.value), dropdown === dropdown2); } catch (_) {}
                    dropdown.value = '';
                };
            }
        });
    }

    // Post to endpoint helper (with button loading state)
    async function postAction(url, bodyData = {}, btnEl = null) {
        const origText = btnEl ? btnEl.innerHTML : null;
        _actionInFlight = true;
        if (btnEl) {
            btnEl.disabled = true;
            btnEl.innerHTML = `<span class="btn-spinner"></span> ${btnEl.textContent.trim()}`;
        }
        try {
            const res = await fetch(`${API_BASE}${url}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(bodyData)
            });
            if (!res.ok) throw new Error("HTTP Action request failed");
            const data = await res.json();
            if (data.success) {
                showToast(data.message, 'success');
            } else {
                showToast(data.message || 'Action failed', 'error');
            }
            // Immediately fetch fresh status after any action
            fetchStatus();
            return data;
        } catch (err) {
            let errorMsg = err.message;
            if (err.message === 'Failed to fetch' || err.name === 'TypeError') {
                errorMsg = 'Cannot reach Python API server. Please run ConnectPhoneUI.app or start ConnectPhoneUI.py in terminal.';
            }
            showToast(`Error: ${errorMsg}`, 'error');
        } finally {
            _actionInFlight = false;
            if (btnEl && origText !== null) {
                btnEl.disabled = false;
                btnEl.innerHTML = origText;
            }
        }
    }

    // Mirror Buttons Click Listeners
    const startMirrorButtons = document.querySelectorAll('.start-mirror-btn');
    startMirrorButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const type = btn.getAttribute('data-type');
            let body = { type: type };
            
            if (type === 'camera') {
                body.camera_facing = document.getElementById('cam-facing').value;
                body.resolution = document.getElementById('cam-res').value;
                body.no_audio = document.getElementById('cam-no-audio').checked;
            }
            
            showToast('Launching scrcpy mirroring stream...', 'info');
            postAction('/api/mirror', body);
        });
    });

    // Camera Control Overlay listeners
    overlayCapture.addEventListener('click', () => {
        showToast('Capturing frame...', 'info');
        postAction('/api/camera/capture');
    });

    overlayRecord.addEventListener('click', () => {
        if (!isRecording) {
            showToast('Starting recording...', 'info');
        } else {
            showToast('Finalizing video clip...', 'info');
        }
        postAction('/api/camera/record_toggle');
    });

    overlayStop.addEventListener('click', () => {
        postAction('/api/mirror/stop');
    });





    // Settings Connections Bindings
    const btnAutoConnect = document.getElementById('btn-conn-autoconnect');
    if (btnAutoConnect) {
        btnAutoConnect.addEventListener('click', () => {
            showToast('<i class="material-symbols-outlined">bolt</i> Trying instant reconnect... scanning if needed.', 'info');
            postAction('/api/connect/auto', {}, btnAutoConnect);
        });
    }

    const btnConnect = document.getElementById('btn-conn-connect');
    if (btnConnect) btnConnect.addEventListener('click', () => {
        const ip = document.getElementById('conn-ip').value.trim();
        const port = document.getElementById('conn-port').value.trim();
        if (!ip || !port) {
            showToast('IP address and the current Wireless Debugging port are required. Use Scan Network to fill them.', 'error');
            return;
        }
        showToast(`Connecting to ${ip}:${port}...`, 'info');
        postAction('/api/connect', { ip: ip, port: port }, btnConnect);
    });

    const btnPair = document.getElementById('btn-conn-pair');
    if (btnPair) btnPair.addEventListener('click', () => {
        const ip = document.getElementById('conn-ip').value.trim();
        const port = document.getElementById('pair-port').value.trim();
        const code = document.getElementById('pair-code').value.trim();
        if (!ip || !port || !code) {
            showToast('IP, Pairing Port, and Pairing Code are all required.', 'error');
            return;
        }
        showToast('Pairing wirelessly with device...', 'info');
        postAction('/api/pair', { ip: ip, port: port, code: code }, btnPair).finally(() => {
            document.getElementById('pair-code').value = '';
        });
    });

    const btnPairQr = document.getElementById('btn-pair-qr');
    const qrPairingPanel = document.getElementById('qr-pairing-panel');
    const qrPairingImage = document.getElementById('qr-pairing-image');
    const qrPairingStatus = document.getElementById('qr-pairing-status');
    let qrPairingPoll = null;
    if (btnPairQr) btnPairQr.addEventListener('click', async () => {
        if (qrPairingPoll) clearInterval(qrPairingPoll);
        btnPairQr.disabled = true;
        try {
            const response = await fetch(`${API_BASE}/api/pair/qr/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{}'
            });
            const data = await response.json();
            if (!data.success) throw new Error(data.message || 'Could not start QR pairing');
            qrPairingImage.src = data.qr_image;
            qrPairingPanel.classList.remove('hidden');
            qrPairingStatus.textContent = 'Waiting for a phone to scan this QR code…';
            const sessionId = data.session_id;
            qrPairingPoll = setInterval(async () => {
                try {
                    const statusResponse = await fetch(`${API_BASE}/api/pair/qr/status?id=${encodeURIComponent(sessionId)}`);
                    const status = await statusResponse.json();
                    qrPairingStatus.textContent = status.message || 'Waiting for scan…';
                    if (['success', 'error', 'expired'].includes(status.status)) {
                        clearInterval(qrPairingPoll);
                        qrPairingPoll = null;
                        btnPairQr.disabled = false;
                        if (status.status === 'success') {
                            showToast(status.message, 'success');
                            fetchStatus(true);
                        } else {
                            showToast(status.message, 'error');
                        }
                    }
                } catch (error) {
                    qrPairingStatus.textContent = `Status check failed: ${error.message}`;
                }
            }, 1000);
        } catch (error) {
            btnPairQr.disabled = false;
            showToast(error.message, 'error');
        }
    });

    const btnDisconnectAll = document.getElementById('btn-disconnect-all');
    if (btnDisconnectAll) btnDisconnectAll.addEventListener('click', () => {
        postAction('/api/disconnect', {}, btnDisconnectAll);
    });

    const btnRestartAdb = document.getElementById('btn-restart-adb');
    if (btnRestartAdb) btnRestartAdb.addEventListener('click', () => {
        showToast('Restarting ADB server...', 'info');
        postAction('/api/restart_adb', {}, btnRestartAdb);
    });

    // mDNS Auto-Discovery Logic
    const btnScanMdns = document.getElementById('btn-scan-mdns-devices');
    const btnConnectAuthorized = document.getElementById('btn-connect-authorized');
    const mdnsList = document.getElementById('mdns-discovered-list');

    if (btnScanMdns && mdnsList) {
        btnScanMdns.addEventListener('click', () => {
            executeMdnsScan();
        });
    }

    if (btnConnectAuthorized) {
        btnConnectAuthorized.addEventListener('click', () => {
            showToast('Discovering phones that already authorize this Mac...', 'info');
            postAction('/api/connect/authorized', {}, btnConnectAuthorized).then(() => {
                fetchStatus(true);
                executeMdnsScan();
            });
        });
    }

    function syncMdnsConnectionButtons() {
        document.querySelectorAll('.btn-mdns-action[data-type="connect"]').forEach(button => {
            const endpoint = `${button.dataset.ip}:${button.dataset.port}`;
            const connected = window.isConnected && endpoint === window.activeDevice;
            button.disabled = connected;
            button.classList.toggle('connected-device', connected);
            button.innerHTML = connected
                ? '<i class="material-symbols-outlined">check_circle</i> Connected'
                : '<i class="material-symbols-outlined">bolt</i> Connect';
        });
    }

    async function executeMdnsScan() {
        mdnsList.innerHTML = `<p class="list-placeholder"><i class="material-symbols-outlined">bolt</i> Scanning Wi-Fi network for active devices...</p>`;
        btnScanMdns.disabled = true;
        try {
            const res = await fetch(`${API_BASE}/api/mdns/discover`);
            const data = await res.json();
            mdnsList.innerHTML = '';
            
            if (data.success && data.services && data.services.length > 0) {
                data.services.forEach(service => {
                    const row = document.createElement('div');
                    row.className = 'device-row';
                    const isPairing = service.type === 'pairing';
                    row.innerHTML = `
                        <div class="device-info-left">
                            <span class="device-type-icon"><i class="material-symbols-outlined">search</i></span>
                            <div class="device-meta">
                                <h4>${escapeHtml(service.name)} (${isPairing ? 'Pairing Service' : 'Connect Target'})</h4>
                                <p>${escapeHtml(service.ip)}:${escapeHtml(service.port)}</p>
                            </div>
                        </div>
                        <div class="device-info-right">
                            <button class="btn btn-sm btn-primary btn-mdns-action" data-ip="${escapeHtml(service.ip)}" data-port="${escapeHtml(service.port)}" data-type="${escapeHtml(service.type)}">
                                ${isPairing ? '<i class="material-symbols-outlined">key</i> Start Pairing' : '<i class="material-symbols-outlined">bolt</i> Connect'}
                            </button>
                        </div>
                    `;
                    
                    const actionBtn = row.querySelector('.btn-mdns-action');
                    actionBtn.addEventListener('click', () => {
                        document.getElementById('conn-ip').value = service.ip;
                        if (isPairing) {
                            document.getElementById('pair-port').value = service.port;
                            document.getElementById('pair-code').value = '';
                            document.getElementById('pair-code').focus();
                            showToast(`Target IP and Pairing Port filled! Please enter the 6-digit Pairing Code shown on your phone.`, 'info');
                        } else {
                            document.getElementById('conn-port').value = service.port;
                            showToast(`Connecting to discovered device at ${service.ip}:${service.port}...`, 'info');
                            postAction('/api/connect', { ip: service.ip, port: service.port }, actionBtn).then(result => {
                                if (result && result.success) {
                                    window.isConnected = true;
                                    window.activeDevice = `${service.ip}:${service.port}`;
                                    syncMdnsConnectionButtons();
                                    fetchStatus();
                                }
                            });
                        }
                    });
                    
                    mdnsList.appendChild(row);
                });
                syncMdnsConnectionButtons();
            } else {
                mdnsList.innerHTML = '<p class="list-placeholder">No active wireless debugging services discovered on local network. Verify "Wireless Debugging" is toggled ON in Developer Options.</p>';
            }
        } catch (err) {
            console.error("mDNS scan error:", err);
            mdnsList.innerHTML = `<p class="list-placeholder error">Scan failed: ${escapeHtml(err.message)}</p>`;
        } finally {
            btnScanMdns.disabled = false;
        }
    }


    const btnRefreshList = document.getElementById('btn-refresh-devices-list');
    if (btnRefreshList) {
        btnRefreshList.addEventListener('click', () => {
            fetchStatus(true);
        });
    }

    // Save Preferences settings Click
    document.getElementById('btn-save-pref').addEventListener('click', () => {
        const body = {
            camera_codec: document.getElementById('pref-codec').value,
            camera_bitrate: document.getElementById('pref-bitrate').value,
            camera_fps: document.getElementById('pref-fps').value,
            audio_preset: document.getElementById('pref-audio-preset').value,
            audio_sync_delay: document.getElementById('pref-sync-delay').value,
            keyboard_mode: document.getElementById('pref-keyboard').value,
            ...(document.getElementById('pref-pin').value.trim() ? { android_pin: document.getElementById('pref-pin').value.trim() } : {}),
            ...(document.getElementById('pref-applock').value.trim() ? { applock_pin: document.getElementById('pref-applock').value.trim() } : {}),
            mirror_enabled: document.getElementById('pref-mirror-enabled').checked,
            screen_off_enabled: document.getElementById('pref-screen-off').checked,
            stay_awake_enabled: document.getElementById('pref-stay-awake').checked,
            show_touches_enabled: document.getElementById('pref-show-touches').checked,
            biometric_daemon_enabled: document.getElementById('pref-biometric-daemon').checked,
            mac_mic_device: document.getElementById('pref-mac-mic-device').value,
            audio_buffer: document.getElementById('pref-audio-buffer').value
        };
        showToast('Saving preferences...', 'info');
        postAction('/api/settings/save', body).then(() => {
            preferencesLoaded = false;
            fetchStatus();
        });
    });



    // --- Live Metrics & Diagnostics ---
    let metricsInterval = null;

    function startMetricsPolling() {
        fetchMetrics();
        if (!metricsInterval) {
            metricsInterval = setInterval(fetchMetrics, 2000);
        }
    }

    function stopMetricsPolling() {
        if (metricsInterval) {
            clearInterval(metricsInterval);
            metricsInterval = null;
        }
    }

    async function fetchMetrics() {
        try {
            const res = await fetch(`${API_BASE}/api/metrics`);
            if (!res.ok) throw new Error("Failed to load metrics");
            const data = await res.json();
            
            if (data.success && data.connected) {
                updateMetricsUI(data);
            }
        } catch (err) {
            console.error("Error fetching metrics:", err);
        }
    }

    function updateMetricsUI(data) {
        // Battery Stats
        const bat = data.battery || {};
        const batPct = bat.level || 0;
        document.getElementById('metric-bat-pct').textContent = `${batPct}%`;
        const batFill = document.getElementById('metric-bat-fill');
        if (batFill) {
            batFill.style.width = `${batPct}%`;
            if (batPct <= 15) {
                batFill.style.background = 'var(--color-danger)';
            } else if (batPct <= 35) {
                batFill.style.background = '#ff9500';
            } else {
                batFill.style.background = 'var(--color-success)';
            }
        }
        document.getElementById('metric-bat-status').textContent = bat.status || '--';
        document.getElementById('metric-bat-health').textContent = bat.health || '--';
        document.getElementById('metric-bat-temp').textContent = bat.temperature ? `${bat.temperature} °C` : '--';
        document.getElementById('metric-bat-voltage').textContent = bat.voltage ? `${bat.voltage} V` : '--';
        document.getElementById('metric-bat-tech').textContent = bat.technology || '--';

        // RAM Stats
        const ram = data.ram || {};
        const ramPct = ram.used_percent || 0;
        document.getElementById('metric-ram-pct').textContent = `${ramPct}%`;
        const ramFill = document.getElementById('metric-ram-fill');
        if (ramFill) {
            ramFill.style.width = `${ramPct}%`;
            if (ramPct >= 85) {
                ramFill.style.background = 'var(--color-danger)';
            } else if (ramPct >= 70) {
                ramFill.style.background = '#ff9500';
            } else {
                ramFill.style.background = 'var(--color-primary)';
            }
        }
        document.getElementById('metric-ram-used').textContent = `${ram.used_gb || 0} GB Used`;
        document.getElementById('metric-ram-total').textContent = `${ram.total_gb || '--'} GB`;
        document.getElementById('metric-ram-avail').textContent = `${ram.avail_gb || '--'} GB`;

        // Storage Stats
        const store = data.storage || {};
        const storePct = store.used_percent || 0;
        document.getElementById('metric-storage-pct').textContent = `${storePct}%`;
        const storeFill = document.getElementById('metric-storage-fill');
        if (storeFill) {
            storeFill.style.width = `${storePct}%`;
            if (storePct >= 90) {
                storeFill.style.background = 'var(--color-danger)';
            } else if (storePct >= 75) {
                storeFill.style.background = '#ff9500';
            } else {
                storeFill.style.background = 'var(--color-primary)';
            }
        }
        document.getElementById('metric-storage-used').textContent = `${store.used_gb || 0} GB Used`;
        document.getElementById('metric-storage-total').textContent = `${store.total_gb || '--'} GB`;
        document.getElementById('metric-storage-avail').textContent = `${store.avail_gb || '--'} GB`;

        // Network Stats
        const net = data.network || {};
        const sys = data.system || {};
        document.getElementById('metric-net-ip').textContent = net.ip || '--';
        document.getElementById('metric-net-type').textContent = net.type || '--';
        document.getElementById('metric-sys-uptime').textContent = sys.uptime || '--';
        document.getElementById('metric-sys-load').textContent = sys.load_average || '--';
    }

    // Ping Test
    const btnPing = document.getElementById('btn-ping-test');
    const pingResult = document.getElementById('ping-test-result');
    if (btnPing) {
        btnPing.addEventListener('click', async () => {
            const manualIp = document.getElementById('conn-ip').value.trim();
            pingResult.classList.remove('hidden');
            pingResult.innerHTML = '<i class="material-symbols-outlined">bolt</i> Running ping test... please wait...';
            pingResult.className = 'ping-result running';
            try {
                const res = await postAction('/api/ping', manualIp ? { ip: manualIp } : {});
                if (res && res.success) {
                    pingResult.textContent = res.message;
                    pingResult.className = 'ping-result success';
                } else {
                    pingResult.textContent = res ? res.message : 'Ping test failed.';
                    pingResult.className = 'ping-result error';
                }
            } catch (err) {
                pingResult.textContent = `Error: ${err.message}`;
                pingResult.className = 'ping-result error';
            }
        });
    }
    const btnPingMetrics = document.getElementById('btn-ping-test-metrics');
    const pingResultMetrics = document.getElementById('ping-test-result-metrics');
    if (btnPingMetrics && pingResultMetrics) {
        btnPingMetrics.addEventListener('click', async () => {
            pingResultMetrics.textContent = 'Running latency test...';
            pingResultMetrics.className = 'ping-result running';
            const manualIp = document.getElementById('conn-ip').value.trim();
            const res = await postAction('/api/ping', manualIp ? { ip: manualIp } : {});
            pingResultMetrics.textContent = res ? res.message : 'Ping test failed.';
            pingResultMetrics.className = `ping-result ${res && res.success ? 'success' : 'error'}`;
        });
    }

    async function loadMacAudioDevices() {
        try {
            const res = await fetch(`${API_BASE}/api/settings/audio_devices`);
            if (!res.ok) throw new Error("Failed to fetch audio devices");
            const data = await res.json();
            if (data.success && data.devices) {
                const select = document.getElementById('pref-mac-mic-device');
                if (select) {
                    select.innerHTML = '<option value="default">Default System Audio Input</option>';
                    data.devices.forEach(dev => {
                        const opt = document.createElement('option');
                        opt.value = dev.index;
                        opt.textContent = `${dev.name} (Index ${dev.index})`;
                        select.appendChild(opt);
                    });
                }
            }
        } catch (err) {
            console.error("Error loading Mac audio devices:", err);
        }
    }


    // Touch ID Unlock Button Listeners
    if (btnPhoneUnlock) {
        btnPhoneUnlock.addEventListener('click', () => {
            showToast("Prompting Touch ID on Mac to unlock phone...", "info");
            postAction('/api/device/unlock');
        });
    }

    // --- Media & Clipboard Sync Logic ---
    const btnRefreshScreenshots = document.getElementById('btn-refresh-screenshots');
    const screenshotsList = document.getElementById('screenshots-list');
    
    if (btnRefreshScreenshots) {
        btnRefreshScreenshots.addEventListener('click', async () => {
            btnRefreshScreenshots.disabled = true;
            screenshotsList.innerHTML = '<p class="list-placeholder">Fetching screenshots...</p>';
            try {
                const res = await fetch(`${API_BASE}/api/screenshots/list`);
                const data = await res.json();
                if (data.success && data.files && data.files.length > 0) {
                    screenshotsList.innerHTML = '';
                    data.files.forEach(filepath => {
                        const filename = filepath.split('/').pop();
                        const div = document.createElement('div');
                        div.className = 'device-row';
                        // Remove the hover animation by un-setting cursor if we just want it to be a list item
                        div.style.cursor = 'default';
                        div.innerHTML = `
                            <div class="device-info-left">
                                <span class="device-type-icon"><i class="material-symbols-outlined">image</i></span>
                                <div class="device-meta">
                            <h4>${escapeHtml(filename)}</h4>
                                </div>
                            </div>
                            <div class="device-info-right">
                                <button class="btn btn-sm btn-primary"><i class="material-symbols-outlined">download</i> Pull</button>
                            </div>
                        `;
                        const pullBtn = div.querySelector('button');
                        pullBtn.addEventListener('click', async () => {
                            const originalText = pullBtn.innerHTML;
                            pullBtn.innerHTML = '<i class="material-symbols-outlined">hourglass_empty</i>';
                            pullBtn.disabled = true;
                            const pullRes = await postAction('/api/screenshots/pull', { path: filepath });
                            if (pullRes && pullRes.success) {
                                showToast(pullRes.message, "success");
                            } else {
                                showToast(pullRes ? pullRes.message : "Failed to pull", "error");
                            }
                            pullBtn.innerHTML = originalText;
                            pullBtn.disabled = false;
                        });
                        screenshotsList.appendChild(div);
                    });
                } else {
                    screenshotsList.innerHTML = `<p class="list-placeholder">No screenshots found or error: ${escapeHtml(data.error || 'None')}</p>`;
                }
            } catch (err) {
                screenshotsList.innerHTML = `<p class="list-placeholder error">Error: ${escapeHtml(err.message)}</p>`;
            } finally {
                btnRefreshScreenshots.disabled = false;
            }
        });
    }

    const btnSyncClipboardStart = document.getElementById('btn-sync-clipboard-start');
    const btnSyncClipboardStop = document.getElementById('btn-sync-clipboard-stop');
    const clipboardSyncStatus = document.getElementById('clipboard-sync-status');

    function updateClipboardBadge(active) {
        if (!clipboardSyncStatus) return;
        if (active) {
            clipboardSyncStatus.style.background = 'rgba(16, 185, 129, 0.1)';
            clipboardSyncStatus.style.border = '1px solid rgba(16, 185, 129, 0.2)';
            clipboardSyncStatus.style.color = 'var(--color-success)';
            clipboardSyncStatus.innerHTML = '<i class="material-symbols-outlined" style="font-size: 16px;">sync</i> <span>Status: Actively Syncing</span>';
        } else {
            clipboardSyncStatus.style.background = 'rgba(239, 68, 68, 0.1)';
            clipboardSyncStatus.style.border = '1px solid rgba(239, 68, 68, 0.2)';
            clipboardSyncStatus.style.color = 'var(--color-danger)';
            clipboardSyncStatus.innerHTML = '<i class="material-symbols-outlined" style="font-size: 16px;">sync_disabled</i> <span>Status: Inactive</span>';
        }
    }

    if (btnSyncClipboardStart) {
        btnSyncClipboardStart.addEventListener('click', () => {
            postAction('/api/clipboard/sync/start').then(res => {
                if(res && res.success) {
                    showToast(res.message, "success");
                    updateClipboardBadge(true);
                }
            });
        });
    }
    if (btnSyncClipboardStop) {
        btnSyncClipboardStop.addEventListener('click', () => {
            postAction('/api/clipboard/sync/stop').then(res => {
                if(res && res.success) {
                    showToast(res.message, "success");
                    updateClipboardBadge(false);
                }
            });
        });
    }

    // Clipboard sync is retired; do not poll a hidden 410 endpoint.

    const btnTypeMacClipboard = document.getElementById('btn-type-mac-clipboard');
    if (btnTypeMacClipboard) {
        btnTypeMacClipboard.addEventListener('click', () => {
            postAction('/api/clipboard/type').then(res => {
                if(res && res.success) showToast(res.message, "success");
            });
        });
    }

    const btnRestartApp = document.getElementById('btn-restart-app');
    if (btnRestartApp) {
        btnRestartApp.addEventListener('click', () => {
            btnRestartApp.disabled = true;
            btnRestartApp.innerHTML = '<i class="material-symbols-outlined">hourglass_empty</i> Restarting...';
            showToast("Restarting application...", "info");
            fetch(`${API_BASE}/api/app/restart`, { method: 'POST' }).catch(e => console.error(e));
            // Reload the window automatically after the backend spins back up
            setTimeout(() => {
                window.location.reload();
            }, 2000);
        });
    }
    // -------------------------------------


    // ─── Termux Terminal Controller ──────────────────────────────────────────────
    const termuxCmdInput = document.getElementById('termux-cmd-input');
    const btnTermuxRun = document.getElementById('btn-termux-run');
    const termuxOutput = document.getElementById('termux-output');
    const btnTermuxClear = document.getElementById('btn-termux-clear');
    const termuxQuickCmds = document.querySelectorAll('.termux-quick-cmd');

    function appendTerminalLine(text, color = '#a9b7c6') {
        if (!termuxOutput) return;
        const line = document.createElement('div');
        line.style.color = color;
        line.style.whiteSpace = 'pre-wrap';
        line.style.fontFamily = 'monospace';
        line.style.marginBottom = '4px';
        line.textContent = text;
        termuxOutput.appendChild(line);
        termuxOutput.scrollTop = termuxOutput.scrollHeight;
    }

    async function executeTermuxCommand(command) {
        if (!command) return;
        appendTerminalLine(`$ ${command}`, '#00ffcc');
        
        try {
            const res = await postAction('/api/termux/execute', { command });
            if (res && res.success) {
                if (res.stdout) {
                    appendTerminalLine(res.stdout, '#e8e8e8');
                }
                if (res.stderr) {
                    appendTerminalLine(res.stderr, '#ff5f56');
                }
                if (res.exit_code !== 0) {
                    appendTerminalLine(`Process exited with code ${res.exit_code}`, '#ffbd2e');
                }
            } else {
                appendTerminalLine(`Error: ${res.message || 'Unknown error'}`, '#ff5f56');
            }
        } catch (err) {
            appendTerminalLine(`Execution failed: ${err}`, '#ff5f56');
        }
    }

    if (btnTermuxRun && termuxCmdInput) {
        btnTermuxRun.addEventListener('click', () => {
            const cmd = termuxCmdInput.value.trim();
            if (cmd) {
                executeTermuxCommand(cmd);
                termuxCmdInput.value = '';
            }
        });

        termuxCmdInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const cmd = termuxCmdInput.value.trim();
                if (cmd) {
                    executeTermuxCommand(cmd);
                    termuxCmdInput.value = '';
                }
            }
        });
    }

    if (btnTermuxClear && termuxOutput) {
        btnTermuxClear.addEventListener('click', () => {
            termuxOutput.innerHTML = `
                <div style="color: #00ffcc;">Welcome to ConnectPhone Termux Console.</div>
                <div style="color: #888; margin-bottom: 15px;">Commands are executed under native Termux sandbox via ADB.</div>
            `;
        });
    }

    termuxQuickCmds.forEach(btn => {
        btn.addEventListener('click', () => {
            const cmd = btn.getAttribute('data-cmd');
            executeTermuxCommand(cmd);
        });
    });

    // ─── Termux Auto Installer ──────────────────────────────────────────────────
    const btnTermuxInstallAuto = document.getElementById('btn-termux-install-auto');
    const termuxInstallProgressStatus = document.getElementById('termux-install-progress-status');
    let termuxInstallPollInterval = null;

    if (btnTermuxInstallAuto) {
        btnTermuxInstallAuto.addEventListener('click', async () => {
            btnTermuxInstallAuto.disabled = true;
            if (termuxInstallProgressStatus) {
                termuxInstallProgressStatus.style.display = 'block';
                termuxInstallProgressStatus.textContent = 'Starting installation...';
                termuxInstallProgressStatus.style.color = '#00ffcc';
            }
            
            try {
                const res = await postAction('/api/termux/install');
                if (res && res.success) {
                    startTermuxInstallPolling();
                } else {
                    btnTermuxInstallAuto.disabled = false;
                    if (termuxInstallProgressStatus) {
                        termuxInstallProgressStatus.textContent = `Error: ${res.message || 'Could not start installation'}`;
                        termuxInstallProgressStatus.style.color = '#ff5f56';
                    }
                }
            } catch (err) {
                btnTermuxInstallAuto.disabled = false;
                if (termuxInstallProgressStatus) {
                    termuxInstallProgressStatus.textContent = `Failed: ${err}`;
                    termuxInstallProgressStatus.style.color = '#ff5f56';
                }
            }
        });
    }

    function startTermuxInstallPolling() {
        if (termuxInstallPollInterval) clearInterval(termuxInstallPollInterval);
        termuxInstallPollInterval = setInterval(async () => {
            try {
                const res = await fetch(`${API_BASE}/api/termux/install/status`);
                const data = await res.json();
                if (data.success) {
                    if (termuxInstallProgressStatus) {
                        termuxInstallProgressStatus.textContent = data.message;
                    }
                    if (data.status === 'success') {
                        clearInterval(termuxInstallPollInterval);
                        btnTermuxInstallAuto.disabled = false;
                        if (termuxInstallProgressStatus) {
                            termuxInstallProgressStatus.style.color = '#27c93f';
                        }
                    } else if (data.status === 'error') {
                        clearInterval(termuxInstallPollInterval);
                        btnTermuxInstallAuto.disabled = false;
                        if (termuxInstallProgressStatus) {
                            termuxInstallProgressStatus.style.color = '#ff5f56';
                        }
                    }
                }
            } catch (err) {
                console.error(err);
            }
        }, 1000);
    }


    // --- TTS Voice Broadcaster ---
    const termuxTtsText = document.getElementById('termux-tts-text');
    const termuxTtsPitch = document.getElementById('termux-tts-pitch');
    const termuxTtsRate = document.getElementById('termux-tts-rate');
    const btnTermuxTts = document.getElementById('btn-termux-tts');
    const ttsPitchVal = document.getElementById('tts-pitch-val');
    const ttsRateVal = document.getElementById('tts-rate-val');

    if (termuxTtsPitch && ttsPitchVal) {
        termuxTtsPitch.addEventListener('input', (e) => { ttsPitchVal.textContent = e.target.value; });
    }
    if (termuxTtsRate && ttsRateVal) {
        termuxTtsRate.addEventListener('input', (e) => { ttsRateVal.textContent = e.target.value; });
    }
    if (btnTermuxTts) {
        btnTermuxTts.addEventListener('click', async () => {
            const text = termuxTtsText ? termuxTtsText.value.trim() : '';
            if (!text) {
                alert('Please type some text to speak.');
                return;
            }
            btnTermuxTts.disabled = true;
            btnTermuxTts.innerHTML = '<i class="material-symbols-outlined">volume_up</i> Speaking...';
            try {
                const pitch = termuxTtsPitch ? termuxTtsPitch.value : 1.0;
                const rate = termuxTtsRate ? termuxTtsRate.value : 1.0;
                const res = await postAction('/api/termux/tts', { text, pitch, rate });
                if (res && res.success) {
                    // Success, silently continue
                } else {
                    alert(res ? res.message : 'Failed to speak text.');
                }
            } catch (err) {
                console.error(err);
            } finally {
                btnTermuxTts.disabled = false;
                btnTermuxTts.innerHTML = '<i class="material-symbols-outlined">volume_up</i> Speak on Phone';
            }
        });
    }

    // --- Live Telemetry Panel ---
    const btnTermuxTelemetry = document.getElementById('btn-termux-telemetry');
    const termuxTelemetryResult = document.getElementById('termux-telemetry-result');

    if (btnTermuxTelemetry) {
        btnTermuxTelemetry.addEventListener('click', async () => {
            btnTermuxTelemetry.disabled = true;
            btnTermuxTelemetry.innerHTML = '<i class="material-symbols-outlined">query_stats</i> Querying Sensors...';
            if (termuxTelemetryResult) termuxTelemetryResult.textContent = 'Querying system telemetry via Termux API...';
            try {
                const res = await postAction('/api/termux/sensors');
                if (res && res.success) {
                    let formatted = '';
                    formatted += `[Battery Status]\n`;
                    formatted += `Level: ${res.battery.percentage || '--'}% | Temp: ${res.battery.temperature ? (res.battery.temperature / 10).toFixed(1) : '--'}°C\n`;
                    formatted += `Health: ${res.battery.health || '--'} | Plugged: ${res.battery.plugged || '--'}\n\n`;
                    
                    formatted += `[Wi-Fi Connection]\n`;
                    formatted += `SSID: ${res.wifi.ssid || '--'} | Speed: ${res.wifi.link_speed_mbps || '--'} Mbps\n`;
                    formatted += `Signal (RSSI): ${res.wifi.rssi || '--'} dBm | State: ${res.wifi.supplicant_state || '--'}\n\n`;
                    
                    formatted += `[Telephony Info]\n`;
                    formatted += `Carrier: ${res.telephony.sim_operator_name || '--'} | Data State: ${res.telephony.data_state || '--'}\n`;
                    formatted += `Network Type: ${res.telephony.network_type || '--'}`;
                    
                    if (termuxTelemetryResult) termuxTelemetryResult.textContent = formatted;
                } else {
                    if (termuxTelemetryResult) termuxTelemetryResult.textContent = `Error: ${res.message}`;
                }
            } catch (err) {
                if (termuxTelemetryResult) termuxTelemetryResult.textContent = `Exception: ${err}`;
            } finally {
                btnTermuxTelemetry.disabled = false;
                btnTermuxTelemetry.innerHTML = '<i class="material-symbols-outlined">query_stats</i> Query Sensors';
            }
        });
    }

    // --- Network Scanner (Nmap) ---
    const btnTermuxScan = document.getElementById('btn-termux-scan');
    const termuxScanTarget = document.getElementById('termux-scan-target');
    const termuxScanType = document.getElementById('termux-scan-type');

    if (btnTermuxScan) {
        btnTermuxScan.addEventListener('click', async () => {
            const target = termuxScanTarget ? termuxScanTarget.value.trim() : '';
            const type = termuxScanType ? termuxScanType.value : 'fast';
            if (!target) {
                alert('Please specify a target IP or subnet (e.g. 192.168.1.1 or 192.168.1.0/24).');
                return;
            }
            btnTermuxScan.disabled = true;
            btnTermuxScan.innerHTML = '<i class="material-symbols-outlined">sync</i> Scanning network...';
            
            if (termuxOutput) {
                const startLine = document.createElement('div');
                startLine.style.color = '#00ffcc';
                startLine.textContent = `\n$ nmap ${type === 'ping' ? '-sn' : (type === 'fast' ? '-F' : '-p 1-1000')} ${target}`;
                termuxOutput.appendChild(startLine);
                const infoLine = document.createElement('div');
                infoLine.style.color = '#a0aec0';
                infoLine.textContent = `Starting active port sweeps using phone's local network controller. Please wait...`;
                termuxOutput.appendChild(infoLine);
                termuxOutput.scrollTop = termuxOutput.scrollHeight;
            }

            try {
                const res = await postAction('/api/termux/scan', { target, type });
                if (res && res.success) {
                    if (termuxOutput) {
                        const outLine = document.createElement('pre');
                        outLine.style.color = '#fff';
                        outLine.style.fontFamily = 'inherit';
                        outLine.style.margin = '10px 0';
                        outLine.style.whiteSpace = 'pre-wrap';
                        outLine.textContent = res.stdout || res.stderr || 'No scan output.';
                        termuxOutput.appendChild(outLine);
                        termuxOutput.scrollTop = termuxOutput.scrollHeight;
                    }
                } else {
                    if (termuxOutput) {
                        const errLine = document.createElement('div');
                        errLine.style.color = '#ff5f56';
                        errLine.textContent = `Scan failed: ${res ? res.message : 'Unknown error'}\nMake sure 'nmap' package is installed in Termux ('pkg install nmap -y').`;
                        termuxOutput.appendChild(errLine);
                        termuxOutput.scrollTop = termuxOutput.scrollHeight;
                    }
                }
            } catch (err) {
                console.error(err);
            } finally {
                btnTermuxScan.disabled = false;
                btnTermuxScan.innerHTML = '<i class="material-symbols-outlined">radar</i> Audit Network';
            }
        });
    }

    // Run Initial Status queries
    if (window.location.protocol === 'file:') {
        const banner = document.getElementById('protocol-warning-banner');
        if (banner) {
            banner.style.display = 'block';
        }
    }
    
    async function initDashboard() {
        await loadMacAudioDevices();
        const initialStatus = await fetchStatus();
        const hasTrustedPhone = (initialStatus?.config?.saved_devices || []).some(device =>
            device && device.auto_reconnect !== false && device.device_serial
        );
        if (initialStatus && !initialStatus.connected && hasTrustedPhone && !sessionStorage.getItem('cp_startup_connect_attempted')) {
            sessionStorage.setItem('cp_startup_connect_attempted', '1');
            showToast('Connecting automatically to your saved phone…', 'info');
            postAction('/api/connect/auto');
        }
        // Keep the dashboard responsive without competing with ADB actions.
        statusInterval = setInterval(fetchStatus, 2500);
    }

    initDashboard();
});

// ==========================================
// AI AUTOMATION & OCR LOGIC
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8282' : '';
    const API_TOKEN = sessionStorage.getItem('cp_api_token') || '';
    const toastContainer = document.getElementById('toast-container');

    function escapeHtml(value) {
        if (value === null || value === undefined) return '';
        return String(value).replace(/[<>&"'`]/g, (ch => {
            return {
                '<': '&lt;',
                '>': '&gt;',
                '&': '&amp;',
                '"': '&quot;',
                "'": '&#39;',
                '`': '&#96;'
            }[ch];
        }));
    }

    function showToast(message, type = 'success') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        
        let icon = '<i class="material-symbols-outlined">notifications</i>';
        if (type === 'success') icon = '<i class="material-symbols-outlined">check_circle</i>';
        else if (type === 'error') icon = '<i class="material-symbols-outlined">cancel</i>';
        else if (type === 'info') icon = '<i class="material-symbols-outlined">hourglass_empty</i>';
        
        toast.innerHTML = `
            ${icon}
            <span class="toast-message">${escapeHtml(message)}</span>
        `;
        
        if (toastContainer) {
            toastContainer.appendChild(toast);
            setTimeout(() => {
                toast.classList.add('show');
            }, 100);
            
            setTimeout(() => {
                toast.classList.remove('show');
                setTimeout(() => {
                    toast.remove();
                }, 300);
            }, 3000);
        }
    }
    
    // OCR Extraction Logic
    const btnOcr = document.getElementById('btn-ai-ocr');
    if (btnOcr) {
        btnOcr.addEventListener('click', async () => {
            const originalText = btnOcr.innerHTML;
            btnOcr.innerHTML = '<span class="status-dot pulse" style="background:#000; width:8px; height:8px; display:inline-block; margin-right:8px;"></span> Analyzing Screen...';
            
            try {
                // Pointing to the new ASGI FastAPI Engine on port 8283
                const response = await fetch('http://localhost:8283/api/action/ocr', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    btnOcr.innerHTML = '✅ Copied to Clipboard!';
                    setTimeout(() => { btnOcr.innerHTML = originalText; }, 3000);
                } else {
                    btnOcr.innerHTML = '❌ OCR Failed';
                    setTimeout(() => { btnOcr.innerHTML = originalText; }, 3000);
                }
            } catch (err) {
                console.error(err);
                btnOcr.innerHTML = '❌ Server Offline';
                setTimeout(() => { btnOcr.innerHTML = originalText; }, 3000);
            }
        });
    }

    // AI Ghost Driver Logic
    const btnAiClick = document.getElementById('btn-ai-click');
    const inputAiTarget = document.getElementById('ai-target-input');
    
    if (btnAiClick && inputAiTarget) {
        btnAiClick.addEventListener('click', async () => {
            const target = inputAiTarget.value.trim();
            if (!target) return;
            
            const originalText = btnAiClick.innerText;
            btnAiClick.innerText = 'Scanning...';
            
            try {
                // Assuming 'currentDeviceIP' is the globally tracked device in index.js
                const serial = window.currentDeviceIP || "192.168.1.5:5555";
                
                const response = await fetch('http://localhost:8283/api/action/ai-click', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target: target, serial: serial })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    btnAiClick.innerText = '✅ Tapped!';
                    inputAiTarget.value = '';
                    setTimeout(() => { btnAiClick.innerText = originalText; }, 2000);
                } else {
                    btnAiClick.innerText = '❌ Not Found';
                    setTimeout(() => { btnAiClick.innerText = originalText; }, 2000);
                }
            } catch (err) {
                console.error(err);
                btnAiClick.innerText = '❌ Error';
                setTimeout(() => { btnAiClick.innerText = originalText; }, 2000);
            }
        });
    }

    // ---------------------------------------------------------
    // Storage Manager Implementation
    // ---------------------------------------------------------
    
    // DOM Elements for Storage Manager
    const storageDisconnectedAlert = document.getElementById('storage-disconnected-alert');
    const storageMainView = document.getElementById('storage-main-view');
    const storageCurrentPath = document.getElementById('storage-current-path');
    const storageBtnBack = document.getElementById('storage-btn-back');
    const storageBtnNewFolder = document.getElementById('storage-btn-new-folder');
    const storageBtnRefresh = document.getElementById('storage-btn-refresh');
    const storageBtnUploadTrigger = document.getElementById('storage-btn-upload-trigger');
    const storageFileUploadInput = document.getElementById('storage-file-upload-input');
    const storageExplorerZone = document.getElementById('storage-explorer-zone');
    const storageDropOverlay = document.getElementById('storage-drop-overlay');
    const storageFileList = document.getElementById('storage-file-list');
    const storageUploadProgressContainer = document.getElementById('storage-upload-progress-container');
    const storageUploadFilename = document.getElementById('storage-upload-filename');
    const storageUploadPercentage = document.getElementById('storage-upload-percentage');
    const storageUploadBar = document.getElementById('storage-upload-bar');

    // New DOM Elements for Selection, Search, Gallery & View toggle
    const storageSelectAll = document.getElementById('storage-select-all');
    const storageSearchInput = document.getElementById('storage-search-input');
    const storageBatchBar = document.getElementById('storage-batch-bar');
    const batchSelectedCount = document.getElementById('batch-selected-count');
    const batchBtnDownload = document.getElementById('batch-btn-download');
    const batchBtnDelete = document.getElementById('batch-btn-delete');
    const batchBtnClear = document.getElementById('batch-btn-clear');

    const galleryModal = document.getElementById('storage-gallery-modal');
    const galleryImg = document.getElementById('gallery-img');
    const galleryClose = document.getElementById('gallery-close');
    const galleryPrev = document.getElementById('gallery-prev');
    const galleryNext = document.getElementById('gallery-next');
    const galleryFilename = document.getElementById('gallery-filename');
    const galleryBtnZoomIn = document.getElementById('gallery-btn-zoom-in');
    const galleryBtnZoomOut = document.getElementById('gallery-btn-zoom-out');
    const galleryBtnRotate = document.getElementById('gallery-btn-rotate');
    const galleryBtnDownload = document.getElementById('gallery-btn-download');

    const storageBtnViewToggle = document.getElementById('storage-btn-view-toggle');
    const storageViewToggleIcon = document.getElementById('storage-view-toggle-icon');
    const storageBrowserCard = document.querySelector('.storage-browser-card');
    const localRootSelect = document.getElementById('local-root-select');
    const localBtnBack = document.getElementById('local-btn-back');
    const localCurrentPath = document.getElementById('local-current-path');
    const localBtnNewFolder = document.getElementById('local-btn-new-folder');
    const localBtnRefresh = document.getElementById('local-btn-refresh');
    const localSearchInput = document.getElementById('local-search-input');
    const localSelectAll = document.getElementById('local-select-all');
    const localFileList = document.getElementById('local-file-list');
    const phoneStorageSelect = document.getElementById('phone-storage-select');
    const transferToPhone = document.getElementById('transfer-to-phone');
    const transferToMac = document.getElementById('transfer-to-mac');
    const transferConflictPolicy = document.getElementById('transfer-conflict-policy');
    const filesShowHidden = document.getElementById('files-show-hidden');
    const filesSortMode = document.getElementById('files-sort-mode');
    const transferQueueSummary = document.getElementById('transfer-queue-summary');
    const transferQueueList = document.getElementById('transfer-queue-list');

    // Storage Manager scoped variables
    let renderFileList, deleteItem, createFolder, uploadFile, downloadFile;
    let storageViewMode = 'list';  // Layout mode: 'list' or 'grid'
    let allFetchedFiles = [];     // Cached in-memory copy of all files in the current folder
    let selectedPaths = [];       // List of currently selected paths
    let currentImageFiles = [];   // List of image files in current folder for gallery slider
    let currentImageIndex = -1;   // Index of the currently previewed image in currentImageFiles
    let zoomLevel = 1;            // Current zoom level for gallery modal preview
    let rotateAngle = 0;          // Current rotation angle for gallery modal preview
    let focusedIndex = -1;        // Track selected index for keyboard controls
    let localFiles = [];
    let localSelectedPaths = [];
    let currentLocalPath = '';
    let refreshedTransferIds = new Set();
    let activeFilePane = 'phone';
    let localFocusedPath = '';
    let hasActiveTransfers = false;

    function sortedFiles(files) {
        const mode = filesSortMode ? filesSortMode.value : 'name-asc';
        const result = [...files];
        result.sort((a, b) => {
            if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
            if (mode === 'name-desc') return b.name.localeCompare(a.name, undefined, { numeric: true, sensitivity: 'base' });
            if (mode === 'date-desc') return (b.mtime || 0) - (a.mtime || 0) || a.name.localeCompare(b.name);
            if (mode === 'size-desc') return (b.size || 0) - (a.size || 0) || a.name.localeCompare(b.name);
            return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
        });
        return result;
    }

    function focusItem(index) {
        const items = document.querySelectorAll('.storage-item');
        if (items.length === 0) return;
        
        // Remove focus from previous item
        if (focusedIndex >= 0 && focusedIndex < items.length) {
            items[focusedIndex].classList.remove('active-focus');
        }
        
        // Clamp index
        if (index < 0) index = 0;
        if (index >= items.length) index = items.length - 1;
        
        focusedIndex = index;
        const targetItem = items[focusedIndex];
        targetItem.classList.add('active-focus');
        
        // Scroll into view if needed
        targetItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    // File utility helpers
    function formatFileSize(bytes) {
        if (bytes === 0 || !bytes) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    function formatDate(epochSeconds) {
        if (!epochSeconds) return 'Unknown';
        const date = new Date(epochSeconds * 1000);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function getFileIconClass(name, isDir) {
        if (isDir) return 'folder item-icon-folder';
        const ext = name.split('.').pop().toLowerCase();
        if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) {
            return 'image item-icon-image';
        }
        if (['mp4', 'mkv', 'avi', 'mov', 'webm', 'flv'].includes(ext)) {
            return 'video_file item-icon-video';
        }
        if (['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'].includes(ext)) {
            return 'audiotrack item-icon-audio';
        }
        if (['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'rtf', 'csv'].includes(ext)) {
            return 'description item-icon-doc';
        }
        return 'draft item-icon-file';
    }

    // Floating batch action bar helper
    function updateBatchActionsBar() {
        if (!storageBatchBar || !batchSelectedCount) return;
        if (selectedPaths.length > 0) {
            batchSelectedCount.innerText = `${selectedPaths.length} item${selectedPaths.length > 1 ? 's' : ''} selected`;
            storageBatchBar.style.display = 'block';
            setTimeout(() => {
                storageBatchBar.classList.add('active');
            }, 10);
        } else {
            storageBatchBar.classList.remove('active');
            setTimeout(() => {
                if (selectedPaths.length === 0) storageBatchBar.style.display = 'none';
            }, 300);
        }
    }

    // Load directory contents
    let storageLoadRetryCount = 0;

    window.loadStorageDirectory = async function(path, isSoft = false, isRetry = false) {
        if (!window.isConnected) return;
        
        if (!isRetry) {
            storageLoadRetryCount = 0;
        }

        window.currentStoragePath = path;
        if (storageCurrentPath) storageCurrentPath.value = path;

        if (storageFileList && !isSoft) {
            storageFileList.innerHTML = `
                <div class="storage-loading">
                    <div class="spinner"></div>
                    <p>Loading files from phone...</p>
                </div>
            `;
        }

        try {
            const hidden = filesShowHidden && filesShowHidden.checked ? '1' : '0';
            const response = await fetch(`${API_BASE}/api/storage/list?path=${encodeURIComponent(path)}&show_hidden=${hidden}`);
            if (!response.ok) throw new Error(`HTTP Error ${response.status}`);
            const data = await response.json();
            
            if (!data.success) {
                throw new Error(data.message || 'Failed to list directory contents');
            }

            // Success, reset retries
            storageLoadRetryCount = 0;

            allFetchedFiles = sortedFiles(data.files || []);
            if (!isSoft) {
                selectedPaths = [];
                updateBatchActionsBar();
                if (storageSelectAll) storageSelectAll.checked = false;
                if (storageSearchInput) storageSearchInput.value = '';
            } else {
                // Filter out selected paths that don't exist anymore
                const currentPaths = allFetchedFiles.map(f => f.path);
                selectedPaths = selectedPaths.filter(p => currentPaths.includes(p));
                updateBatchActionsBar();
            }

            // Extract image files in the current folder for the gallery slider
            currentImageFiles = allFetchedFiles.filter(file => {
                if (file.is_dir) return false;
                const ext = file.name.split('.').pop().toLowerCase();
                return ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(ext);
            });

            if (isSoft && storageSearchInput && storageSearchInput.value) {
                const query = storageSearchInput.value.toLowerCase().trim();
                const filtered = allFetchedFiles.filter(file => file.name.toLowerCase().includes(query));
                renderFileList(filtered);
            } else {
                renderFileList(allFetchedFiles);
            }
        } catch (err) {
            console.error('Failed to load directory:', err);
            
            // Retry if connected
            if (window.isConnected && storageLoadRetryCount < 5) {
                storageLoadRetryCount++;
                if (storageFileList && !isSoft) {
                    storageFileList.innerHTML = `
                        <div class="storage-loading">
                            <div class="spinner"></div>
                            <p>Loading files from phone... (Attempt ${storageLoadRetryCount}/5)</p>
                        </div>
                    `;
                }
                setTimeout(() => {
                    if (window.currentStoragePath === path) {
                        window.loadStorageDirectory(path, isSoft, true);
                    }
                }, 1500);
            } else {
                if (!isSoft) {
                    storageFileList.innerHTML = `
                        <div class="storage-loading">
                            <i class="material-symbols-outlined" style="font-size:48px; color:var(--color-danger)">cloud_off</i>
                            <p>Failed to connect to phone storage. Ensure device is unlocked and authorized.</p>
                            <button class="btn btn-sm btn-ghost" onclick="window.loadStorageDirectory(window.currentStoragePath)" style="margin-top: 10px;">
                                <i class="material-symbols-outlined">refresh</i> Retry Now
                            </button>
                        </div>
                    `;
                }
            }
        }
    }


    renderFileList = function(files) {
        if (!storageFileList) return;
        focusedIndex = -1; // Reset keyboard focus
        if (files.length === 0) {
            storageFileList.innerHTML = `
                <div class="storage-loading">
                    <i class="material-symbols-outlined" style="font-size:48px; color:var(--text-muted)">folder_open</i>
                    <p>No items found.</p>
                </div>
            `;
            return;
        }

        storageFileList.innerHTML = '';
        files.forEach((file, index) => {
            const row = document.createElement('div');
            row.className = 'storage-item';
            row.setAttribute('tabindex', '0');
            
            row.addEventListener('click', () => {
                activeFilePane = 'phone';
                focusItem(index);
            });
            
            const downloadUrl = '#';
            const iconClass = getFileIconClass(file.name, file.is_dir);
            const sizeStr = file.is_dir ? '--' : formatFileSize(file.size);
            const dateStr = formatDate(file.mtime);

            // Columns structure (includes row checkbox)
            row.innerHTML = `
                <div class="col-checkbox">
                    <input type="checkbox" class="storage-checkbox storage-item-checkbox" data-path="${escapeHtml(file.path)}" ${selectedPaths.includes(file.path) ? 'checked' : ''}>
                </div>
                <div class="col-name">
                    <a href="${downloadUrl}" class="col-name-link" draggable="false" title="Double-click to open" style="color: inherit; text-decoration: none; display: inline-flex; align-items: center; gap: 8px; width: 100%;">
                        <i class="material-symbols-outlined ${iconClass}" style="flex-shrink: 0;">${file.is_dir ? 'folder' : 'draft'}</i>
                        <span class="col-name-text" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(file.name)}</span>
                    </a>
                </div>
                <div class="col-size">${sizeStr}</div>
                <div class="col-date">${dateStr}</div>
                <div class="col-actions">
                    ${!file.is_dir ? `
                        <button class="btn btn-icon-only btn-download" title="Download file" style="padding:4px; display:inline-flex;">
                            <i class="material-symbols-outlined" style="font-size:18px;">download</i>
                        </button>
                    ` : ''}
                    <button class="btn btn-icon-only btn-rename" title="Rename item" style="padding:4px; display:inline-flex;">
                        <i class="material-symbols-outlined" style="font-size:18px;">drive_file_rename_outline</i>
                    </button>
                    <button class="btn btn-icon-only btn-delete" title="Delete item" style="padding:4px; display:inline-flex; border-color: rgba(240, 107, 120, 0.2); color: var(--color-danger);">
                        <i class="material-symbols-outlined" style="font-size:18px;">delete</i>
                    </button>
                </div>
            `;

            // Double click to enter directory, or preview image
            if (file.is_dir) {
                row.addEventListener('dblclick', () => {
                    window.loadStorageDirectory(file.path);
                });
            } else {
                const ext = file.name.split('.').pop().toLowerCase();
                if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(ext)) {
                    row.addEventListener('dblclick', () => {
                        openGalleryPreview(file.path);
                    });
                } else {
                    row.addEventListener('dblclick', () => {
                        downloadFile(file.path, file.name);
                    });
                }
            }

            // Prevent navigation; authenticated downloads use fetch or the Download button.
            const link = row.querySelector('.col-name-link');
            if (link) {
                link.addEventListener('click', (e) => e.preventDefault());
            }

            // Stop click propagation on checkbox
            const itemCheckbox = row.querySelector('.storage-item-checkbox');
            if (itemCheckbox) {
                itemCheckbox.addEventListener('click', (e) => {
                    e.stopPropagation();
                });
                itemCheckbox.addEventListener('change', () => {
                    if (itemCheckbox.checked) {
                        if (!selectedPaths.includes(file.path)) selectedPaths.push(file.path);
                    } else {
                        selectedPaths = selectedPaths.filter(p => p !== file.path);
                    }
                    updateBatchActionsBar();
                });
            }

            // Download handler
            if (!file.is_dir) {
                row.querySelector('.btn-download').addEventListener('click', (e) => {
                    e.stopPropagation();
                    downloadFile(file.path, file.name);
                });
            }

            row.querySelector('.btn-rename').addEventListener('click', async (e) => {
                e.stopPropagation();
                const name = prompt('Rename item:', file.name);
                if (!name || name.trim() === file.name) return;
                try {
                    const response = await fetch(`${API_BASE}/api/files/remote/rename`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: file.path, name: name.trim() })
                    });
                    const res = await response.json();
                    showToast(res.message || (res.success ? 'Item renamed' : 'Rename failed'), res.success ? 'success' : 'error');
                    if (res.success) window.loadStorageDirectory(window.currentStoragePath);
                } catch (err) {
                    showToast(`Rename failed: ${err.message}`, 'error');
                }
            });

            // Delete handler
            row.querySelector('.btn-delete').addEventListener('click', (e) => {
                e.stopPropagation();
                if (confirm(`Are you sure you want to delete "${file.name}"?`)) {
                    deleteItem(file.path);
                }
            });

            storageFileList.appendChild(row);
        });
    }

    // Delete item
    deleteItem = async function(path) {
        try {
            const response = await fetch(`${API_BASE}/api/storage/delete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: path })
            });
            const res = await response.json();
            if (res.success) {
                showToast(res.message || 'Item deleted successfully', 'success');
                window.loadStorageDirectory(window.currentStoragePath);
            } else {
                showToast(res.message || 'Delete failed', 'error');
            }
        } catch (err) {
            console.error('Delete failed:', err);
            showToast('Network error during delete', 'error');
        }
    }

    // Create folder
    createFolder = async function(name) {
        try {
            const response = await fetch(`${API_BASE}/api/storage/mkdir`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ parent: window.currentStoragePath, name: name })
            });
            const res = await response.json();
            if (res.success) {
                showToast(res.message || 'Folder created successfully', 'success');
                window.loadStorageDirectory(window.currentStoragePath);
            } else {
                showToast(res.message || 'Failed to create folder', 'error');
            }
        } catch (err) {
            console.error('Mkdir failed:', err);
            showToast('Network error during folder creation', 'error');
        }
    }

    // Upload files sequentially with progress bar support
    uploadFiles = async function(fileObjects) {
        if (!fileObjects || fileObjects.length === 0) return;
        
        // Normalize input: convert File objects array to { file, relativePath: "" } objects array
        const normalized = Array.from(fileObjects).map(item => {
            if (item instanceof File) {
                return { file: item, relativePath: "" };
            }
            return item;
        });
        
        for (let i = 0; i < normalized.length; i++) {
            const fileObj = normalized[i];
            const file = fileObj.file;
            const relativePath = fileObj.relativePath || "";
            const displayName = relativePath || file.name;
            
            await new Promise((resolve) => {
                const formData = new FormData();
                formData.append('path', window.currentStoragePath);
                formData.append('file', file);
                if (relativePath) {
                    formData.append('relativePath', relativePath);
                }
                
                if (storageUploadProgressContainer) {
                    storageUploadProgressContainer.style.display = 'block';
                    if (storageUploadFilename) {
                        storageUploadFilename.innerText = normalized.length > 1 
                            ? `[${i + 1}/${normalized.length}] ${displayName}` 
                            : displayName;
                    }
                    if (storageUploadPercentage) storageUploadPercentage.innerText = '0%';
                    if (storageUploadBar) storageUploadBar.style.width = '0%';
                }
                
                const xhr = new XMLHttpRequest();
                xhr.open('POST', `${API_BASE}/api/storage/upload`, true);
                if (API_TOKEN) {
                    xhr.setRequestHeader('X-ConnectPhone-Token', API_TOKEN);
                }
                
                xhr.upload.addEventListener('progress', (e) => {
                    if (e.lengthComputable && storageUploadPercentage && storageUploadBar) {
                        const percent = Math.round((e.loaded / e.total) * 100);
                        storageUploadPercentage.innerText = `${percent}%`;
                        storageUploadBar.style.width = `${percent}%`;
                    }
                });
                
                xhr.onload = () => {
                    if (xhr.status === 200) {
                        try {
                            const res = JSON.parse(xhr.responseText);
                            if (res.success) {
                                showToast(`Uploaded: ${file.name}`, 'success');
                            } else {
                                showToast(`Failed to upload ${file.name}: ${res.message}`, 'error');
                            }
                        } catch (err) {
                            showToast(`Uploaded: ${file.name}`, 'success');
                        }
                    } else {
                        showToast(`Failed to upload ${file.name} (HTTP ${xhr.status})`, 'error');
                    }
                    resolve();
                };
                
                xhr.onerror = () => {
                    showToast(`Error uploading: ${file.name}`, 'error');
                    resolve();
                };
                
                xhr.send(formData);
            });
        }
        
        if (storageUploadProgressContainer) storageUploadProgressContainer.style.display = 'none';
        window.loadStorageDirectory(window.currentStoragePath);
    }

    downloadFile = async function(remotePath, filename) {
        try {
            showToast(`Downloading: ${filename}...`, 'info');
            const response = await fetch(`${API_BASE}/api/storage/download_external`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-ConnectPhone-Token': API_TOKEN
                },
                body: JSON.stringify({ path: remotePath })
            });
            const res = await response.json();
            if (res.success) {
                showToast(res.message || 'Download finished successfully!', 'success');
            } else {
                throw new Error(res.message || 'Server error');
            }
        } catch (err) {
            console.error('Download failed:', err);
            showToast(`Download failed: ${err.message}`, 'error');
        }
    }

    // Image Gallery Preview Helper Logic
    async function openGalleryPreview(filePath) {
        if (!galleryModal || !galleryImg || !galleryFilename) return;
        
        currentImageIndex = currentImageFiles.findIndex(file => file.path === filePath);
        if (currentImageIndex === -1) return;
        
        zoomLevel = 1;
        rotateAngle = 0;
        applyGalleryTransforms();
        
        galleryModal.style.display = 'flex';
        setTimeout(() => {
            galleryModal.classList.add('active');
        }, 50);

        await loadGalleryImage(filePath);
        updateGalleryNavButtons();
    }

    async function loadGalleryImage(filePath) {
        if (!galleryImg || !galleryFilename) return;
        
        const filename = filePath.split('/').pop();
        galleryFilename.innerText = filename;
        galleryImg.src = '';
        galleryImg.style.opacity = '0.3';
        
        try {
            const response = await fetch(`${API_BASE}/api/storage/download?path=${encodeURIComponent(filePath)}`, {
                headers: { 'X-ConnectPhone-Token': API_TOKEN }
            });
            if (!response.ok) throw new Error(`HTTP Error ${response.status}`);
            
            const blob = await response.blob();
            const objectUrl = window.URL.createObjectURL(blob);
            galleryImg.src = objectUrl;
            galleryImg.style.opacity = '1';
        } catch (err) {
            console.error('Failed to load image preview:', err);
            showToast('Failed to load image preview', 'error');
        }
    }

    function updateGalleryNavButtons() {
        if (!galleryPrev || !galleryNext) return;
        galleryPrev.style.display = currentImageIndex > 0 ? 'inline-flex' : 'none';
        galleryNext.style.display = currentImageIndex < currentImageFiles.length - 1 ? 'inline-flex' : 'none';
    }

    function applyGalleryTransforms() {
        if (!galleryImg) return;
        galleryImg.style.transform = `scale(${zoomLevel}) rotate(${rotateAngle}deg)`;
    }

    function closeGalleryModal() {
        if (!galleryModal || !galleryImg) return;
        galleryModal.classList.remove('active');
        setTimeout(() => {
            galleryModal.style.display = 'none';
            if (galleryImg.src.startsWith('blob:')) {
                window.URL.revokeObjectURL(galleryImg.src);
            }
            galleryImg.src = '';
        }, 300);
    }

    // Keyboard Shortcuts for Gallery Modal and Explorer Navigation
    window.addEventListener('keydown', (e) => {
        if (galleryModal && galleryModal.classList.contains('active')) {
            if (e.key === 'Escape') {
                closeGalleryModal();
            } else if (e.key === 'ArrowLeft') {
                if (galleryPrev && galleryPrev.style.display !== 'none') {
                    galleryPrev.click();
                }
            } else if (e.key === 'ArrowRight') {
                if (galleryNext && galleryNext.style.display !== 'none') {
                    galleryNext.click();
                }
            }
        } else {
            // Ignore events if user is focused inside input elements
            const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
            if (activeTag === 'input' || activeTag === 'textarea') {
                return;
            }

            if (activeFilePane === 'local') {
                const isCommand = e.metaKey || e.ctrlKey;
                if (isCommand && e.key.toLowerCase() === 'a') {
                    e.preventDefault();
                    if (localSelectAll) { localSelectAll.checked = true; localSelectAll.dispatchEvent(new Event('change')); }
                } else if (isCommand && e.key.toLowerCase() === 'r') {
                    e.preventDefault();
                    loadLocalDirectory(currentLocalPath);
                } else if (e.key === 'Backspace') {
                    e.preventDefault();
                    loadLocalDirectory(localParent(currentLocalPath));
                } else if (e.key === 'F2' || (e.metaKey && e.key === 'Enter')) {
                    e.preventDefault();
                    const row = [...document.querySelectorAll('.local-file-item')].find(item => item.dataset.path === localFocusedPath);
                    const rename = row && row.querySelector('.local-rename');
                    if (rename) rename.click();
                } else if (e.key === 'Enter' && localFocusedPath) {
                    const file = localFiles.find(item => item.path === localFocusedPath);
                    if (file && file.is_dir) loadLocalDirectory(file.path);
                } else if ((e.key === ' ' || e.key === 'Spacebar') && localFocusedPath) {
                    e.preventDefault();
                    const row = [...document.querySelectorAll('.local-file-item')].find(item => item.dataset.path === localFocusedPath);
                    const checkbox = row && row.querySelector('.local-item-checkbox');
                    if (checkbox) { checkbox.checked = !checkbox.checked; checkbox.dispatchEvent(new Event('change')); }
                }
                return;
            }
            
            if (e.key === 'Backspace' || e.key === 'Delete') {
                if (storageBtnBack) {
                    e.preventDefault();
                    storageBtnBack.click();
                }
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                const itemsCount = document.querySelectorAll('.storage-item').length;
                if (itemsCount > 0) {
                    if (focusedIndex === -1) {
                        focusItem(0);
                    } else if (focusedIndex < itemsCount - 1) {
                        focusItem(focusedIndex + 1);
                    }
                }
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                const itemsCount = document.querySelectorAll('.storage-item').length;
                if (itemsCount > 0) {
                    if (focusedIndex === -1) {
                        focusItem(itemsCount - 1);
                    } else if (focusedIndex > 0) {
                        focusItem(focusedIndex - 1);
                    }
                }
            } else if (e.key === 'Enter') {
                if (focusedIndex !== -1 && allFetchedFiles[focusedIndex]) {
                    e.preventDefault();
                    const file = allFetchedFiles[focusedIndex];
                    if (file.is_dir) {
                        window.loadStorageDirectory(file.path);
                    } else {
                        const ext = file.name.split('.').pop().toLowerCase();
                        if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(ext)) {
                            openGalleryPreview(file.path);
                        }
                    }
                }
            } else if (e.key === ' ' || e.key === 'Spacebar') {
                if (focusedIndex !== -1) {
                    e.preventDefault();
                    const items = document.querySelectorAll('.storage-item');
                    if (items[focusedIndex]) {
                        const cb = items[focusedIndex].querySelector('.storage-item-checkbox');
                        if (cb) {
                            cb.checked = !cb.checked;
                            cb.dispatchEvent(new Event('change'));
                        }
                    }
                }
            }
        }
    });

    // Event listeners
    if (storageBtnBack) {
        storageBtnBack.addEventListener('click', () => {
            if (window.currentStoragePath === '/sdcard' || window.currentStoragePath === '/' || window.currentStoragePath === '/storage/emulated/0') {
                showToast('Already at root directory', 'info');
                return;
            }
            const parts = window.currentStoragePath.split('/');
            parts.pop();
            let parentPath = parts.join('/');
            if (!parentPath) parentPath = '/';
            window.loadStorageDirectory(parentPath);
        });
    }

    if (storageCurrentPath) {
        storageCurrentPath.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const path = storageCurrentPath.value.trim();
                if (path) window.loadStorageDirectory(path);
            }
        });
    }

    if (storageBtnNewFolder) {
        storageBtnNewFolder.addEventListener('click', () => {
            const name = prompt('Enter new folder name:');
            if (name) {
                const cleanName = name.trim();
                if (cleanName) createFolder(cleanName);
            }
        });
    }

    if (storageBtnRefresh) {
        storageBtnRefresh.addEventListener('click', () => {
            window.loadStorageDirectory(window.currentStoragePath);
        });
    }

    if (storageBtnUploadTrigger) {
        storageBtnUploadTrigger.addEventListener('click', () => {
            if (storageFileUploadInput) storageFileUploadInput.click();
        });
    }

    if (storageFileUploadInput) {
        storageFileUploadInput.addEventListener('change', () => {
            const files = storageFileUploadInput.files;
            if (files && files.length > 0) {
                uploadFiles(Array.from(files));
                storageFileUploadInput.value = '';
            }
        });
    }

    // Select all handler
    if (storageSelectAll) {
        storageSelectAll.addEventListener('change', () => {
            const isChecked = storageSelectAll.checked;
            const checkboxes = document.querySelectorAll('.storage-item-checkbox');
            
            selectedPaths = [];
            checkboxes.forEach(cb => {
                cb.checked = isChecked;
                const path = cb.getAttribute('data-path');
                if (isChecked && path) {
                    selectedPaths.push(path);
                }
            });
            updateBatchActionsBar();
        });
    }

    // Cancel batch actions handler
    if (batchBtnClear) {
        batchBtnClear.addEventListener('click', () => {
            selectedPaths = [];
            updateBatchActionsBar();
            const checkboxes = document.querySelectorAll('.storage-item-checkbox');
            checkboxes.forEach(cb => cb.checked = false);
            if (storageSelectAll) storageSelectAll.checked = false;
        });
    }

    // Real-time search filter input handler
    if (storageSearchInput) {
        storageSearchInput.addEventListener('input', () => {
            const query = storageSearchInput.value.toLowerCase().trim();
            const filtered = allFetchedFiles.filter(file => file.name.toLowerCase().includes(query));
            renderFileList(filtered);
        });
    }

    // View toggle handler (List/Grid view)
    if (storageBtnViewToggle && storageBrowserCard && storageViewToggleIcon) {
        storageBtnViewToggle.addEventListener('click', () => {
            if (storageViewMode === 'list') {
                storageViewMode = 'grid';
                storageBrowserCard.classList.add('grid-mode');
                storageViewToggleIcon.innerText = 'view_list';
                storageBtnViewToggle.title = 'Switch to List View';
            } else {
                storageViewMode = 'list';
                storageBrowserCard.classList.remove('grid-mode');
                storageViewToggleIcon.innerText = 'grid_view';
                storageBtnViewToggle.title = 'Switch to Grid View';
            }
        });
    }

    // Batch download handler
    if (batchBtnDownload) {
        batchBtnDownload.addEventListener('click', async () => {
            if (selectedPaths.length === 0) return;
            
            const originalText = batchBtnDownload.innerHTML;
            batchBtnDownload.disabled = true;
            batchBtnDownload.innerHTML = '<span class="status-dot pulse" style="background:#fff; width:8px; height:8px; display:inline-block; margin-right:8px;"></span> Building ZIP...';
            showToast('Preparing batch download... please wait.', 'info');

            try {
                const response = await fetch(`${API_BASE}/api/storage/download_zip_external`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-ConnectPhone-Token': API_TOKEN
                    },
                    body: JSON.stringify({ paths: selectedPaths })
                });

                const res = await response.json();
                if (res.success) {
                    showToast(res.message || 'Saved ZIP file to Downloads folder!', 'success');
                    selectedPaths = [];
                    updateBatchActionsBar();
                    const checkboxes = document.querySelectorAll('.storage-item-checkbox');
                    checkboxes.forEach(cb => cb.checked = false);
                    if (storageSelectAll) storageSelectAll.checked = false;
                } else {
                    throw new Error(res.message || 'Server error');
                }
            } catch (err) {
                console.error('Batch download failed:', err);
                showToast(`Failed to compile files for download: ${err.message}`, 'error');
            } finally {
                batchBtnDownload.disabled = false;
                batchBtnDownload.innerHTML = originalText;
            }
        });
    }

    // Batch delete handler
    if (batchBtnDelete) {
        batchBtnDelete.addEventListener('click', async () => {
            if (selectedPaths.length === 0) return;
            const confirmMsg = `Are you sure you want to delete the ${selectedPaths.length} selected item(s)? This action cannot be undone.`;
            if (confirm(confirmMsg)) {
                const originalText = batchBtnDelete.innerHTML;
                batchBtnDelete.disabled = true;
                batchBtnDelete.innerHTML = '<span class="status-dot pulse" style="background:#fff; width:8px; height:8px; display:inline-block; margin-right:8px;"></span> Deleting...';
                
                try {
                    const response = await fetch(`${API_BASE}/api/storage/delete_multiple`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-ConnectPhone-Token': API_TOKEN
                        },
                        body: JSON.stringify({ paths: selectedPaths })
                    });
                    const res = await response.json();
                    
                    if (res.success) {
                        showToast(res.message || 'Items deleted successfully', 'success');
                        selectedPaths = [];
                        updateBatchActionsBar();
                        if (storageSelectAll) storageSelectAll.checked = false;
                        window.loadStorageDirectory(window.currentStoragePath);
                    } else {
                        showToast(res.message || 'Failed to delete items', 'error');
                    }
                } catch (err) {
                    console.error('Batch delete failed:', err);
                    showToast('Network error during batch delete', 'error');
                } finally {
                    batchBtnDelete.disabled = false;
                    batchBtnDelete.innerHTML = originalText;
                }
            }
        });
    }

    // Gallery controller listeners
    if (galleryClose) {
        galleryClose.addEventListener('click', (e) => {
            e.preventDefault();
            closeGalleryModal();
        });
    }

    if (galleryPrev) {
        galleryPrev.addEventListener('click', async (e) => {
            e.preventDefault();
            if (currentImageIndex > 0) {
                currentImageIndex--;
                zoomLevel = 1;
                rotateAngle = 0;
                applyGalleryTransforms();
                await loadGalleryImage(currentImageFiles[currentImageIndex].path);
                updateGalleryNavButtons();
            }
        });
    }

    if (galleryNext) {
        galleryNext.addEventListener('click', async (e) => {
            e.preventDefault();
            if (currentImageIndex < currentImageFiles.length - 1) {
                currentImageIndex++;
                zoomLevel = 1;
                rotateAngle = 0;
                applyGalleryTransforms();
                await loadGalleryImage(currentImageFiles[currentImageIndex].path);
                updateGalleryNavButtons();
            }
        });
    }

    if (galleryBtnZoomIn) {
        galleryBtnZoomIn.addEventListener('click', (e) => {
            e.preventDefault();
            if (zoomLevel < 3) {
                zoomLevel += 0.25;
                applyGalleryTransforms();
            }
        });
    }

    if (galleryBtnZoomOut) {
        galleryBtnZoomOut.addEventListener('click', (e) => {
            e.preventDefault();
            if (zoomLevel > 0.5) {
                zoomLevel -= 0.25;
                applyGalleryTransforms();
            }
        });
    }

    if (galleryBtnRotate) {
        galleryBtnRotate.addEventListener('click', (e) => {
            e.preventDefault();
            rotateAngle = (rotateAngle + 90) % 360;
            applyGalleryTransforms();
        });
    }

    if (galleryBtnDownload) {
        galleryBtnDownload.addEventListener('click', (e) => {
            e.preventDefault();
            if (currentImageIndex !== -1) {
                const img = currentImageFiles[currentImageIndex];
                downloadFile(img.path, img.name);
            }
        });
    }

    // Drag and Drop (Mac to Phone)
    if (storageExplorerZone && storageDropOverlay) {
        let dragCounter = 0;

        window.addEventListener('dragenter', (e) => {
            // Only show overlay if we are dragging files and in the storage tab
            const activeTabButton = document.querySelector('.nav-btn.active');
            const activeTab = activeTabButton ? activeTabButton.getAttribute('data-tab') : '';
            if (activeTab !== 'storage') return;

            e.preventDefault();
            dragCounter++;
            if (dragCounter === 1) {
                storageDropOverlay.classList.add('active');
            }
        });

        window.addEventListener('dragover', (e) => {
            const activeTabButton = document.querySelector('.nav-btn.active');
            const activeTab = activeTabButton ? activeTabButton.getAttribute('data-tab') : '';
            if (activeTab !== 'storage') return;
            e.preventDefault();
        });

        window.addEventListener('dragleave', (e) => {
            const activeTabButton = document.querySelector('.nav-btn.active');
            const activeTab = activeTabButton ? activeTabButton.getAttribute('data-tab') : '';
            if (activeTab !== 'storage') return;
            
            dragCounter--;
            if (dragCounter <= 0) {
                dragCounter = 0;
                storageDropOverlay.classList.remove('active');
            }
        });

        window.addEventListener('drop', async (e) => {
            const activeTabButton = document.querySelector('.nav-btn.active');
            const activeTab = activeTabButton ? activeTabButton.getAttribute('data-tab') : '';
            if (activeTab !== 'storage') return;

            e.preventDefault();
            dragCounter = 0;
            storageDropOverlay.classList.remove('active');
            
            const dt = e.dataTransfer;
            if (dt.items && dt.items.length > 0) {
                const entries = [];
                for (let i = 0; i < dt.items.length; i++) {
                    if (dt.items[i].kind === 'file') {
                        const entry = dt.items[i].webkitGetAsEntry();
                        if (entry) {
                            entries.push(entry);
                        }
                    }
                }
                
                if (entries.length > 0) {
                    showToast("Scanning folder contents...", "info");
                    
                    // Recursive directory reader helper
                    async function getFilesFromEntries(entriesList) {
                        const fileObjects = [];
                        async function traverse(entry, path = "") {
                            if (entry.isFile) {
                                const file = await new Promise((resFile, rejFile) => {
                                    entry.file(resFile, rejFile);
                                });
                                fileObjects.push({
                                    file: file,
                                    relativePath: path + file.name
                                });
                            } else if (entry.isDirectory) {
                                const dirReader = entry.createReader();
                                const readEntries = async () => {
                                    return new Promise((resolveEntries) => {
                                        dirReader.readEntries((results) => {
                                            resolveEntries(results || []);
                                        });
                                    });
                                };
                                
                                let results = await readEntries();
                                let allResults = [...results];
                                while (results.length > 0) {
                                    results = await readEntries();
                                    if (results.length > 0) {
                                        allResults = allResults.concat(results);
                                    }
                                }
                                
                                for (const childEntry of allResults) {
                                    await traverse(childEntry, path + entry.name + "/");
                                }
                            }
                        }
                        for (const ent of entriesList) {
                            await traverse(ent);
                        }
                        return fileObjects;
                    }
                    
                    try {
                        const filesToUpload = await getFilesFromEntries(entries);
                        if (filesToUpload.length > 0) {
                            uploadFiles(filesToUpload);
                        } else {
                            showToast("No files found to upload.", "warning");
                        }
                    } catch (err) {
                        console.error("Error reading folder:", err);
                        showToast("Failed to read dropped folder contents.", "error");
                    }
                }
            } else if (dt.files && dt.files.length > 0) {
                uploadFiles(Array.from(dt.files));
            }
        });
    }

    // Trackpad gestures (horizontal swipe back in directory list)
    let dirSwipeCooldown = false;
    function handleDirSwipe(deltaX) {
        if (dirSwipeCooldown) return;
        if (deltaX < -25) { // Left-to-right swipe (Back)
            dirSwipeCooldown = true;
            setTimeout(() => { dirSwipeCooldown = false; }, 400);
            if (storageBtnBack) {
                storageBtnBack.click();
            }
        }
    }

    if (storageFileList) {
        storageFileList.addEventListener('wheel', (e) => {
            const absX = Math.abs(e.deltaX);
            const absY = Math.abs(e.deltaY);
            if (absX > 15 && absX > absY * 1.5 && !e.ctrlKey) {
                handleDirSwipe(e.deltaX);
            }
        });
    }

    // Trackpad gestures inside image previewer (pinch-to-zoom & two-finger swipe navigations)
    let swipeCooldown = false;
    function handleGallerySwipe(deltaX) {
        if (swipeCooldown) return;
        swipeCooldown = true;
        setTimeout(() => { swipeCooldown = false; }, 350);
        
        if (deltaX < 0) {
            if (galleryPrev && galleryPrev.style.display !== 'none') {
                galleryPrev.click();
            }
        } else {
            if (galleryNext && galleryNext.style.display !== 'none') {
                galleryNext.click();
            }
        }
    }

    const galleryImageWrapper = document.querySelector('.gallery-image-wrapper');
    if (galleryImageWrapper) {
        galleryImageWrapper.addEventListener('wheel', (e) => {
            if (e.ctrlKey) {
                // Pinch gesture
                e.preventDefault();
                // Proportional velocity-based zoom scaling
                const delta = -e.deltaY * 0.008;
                zoomLevel = Math.max(0.5, Math.min(3.5, zoomLevel + delta));
                applyGalleryTransforms();
            } else {
                // Two-finger swipe gesture
                const absX = Math.abs(e.deltaX);
                const absY = Math.abs(e.deltaY);
                if (absX > 15 && absX > absY * 1.5) {
                    e.preventDefault();
                    handleGallerySwipe(e.deltaX);
                }
            }
        }, { passive: false });
    }

    // OpenMTP-style local pane and queued bidirectional transfers
    async function postJson(path, body) {
        const response = await fetch(`${API_BASE}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-ConnectPhone-Token': API_TOKEN },
            body: JSON.stringify(body)
        });
        return response.json();
    }

    function localParent(path) {
        const clean = String(path || '').replace(/\/$/, '');
        const index = clean.lastIndexOf('/');
        return index <= 0 ? '/' : clean.slice(0, index);
    }

    function renderLocalFiles() {
        if (!localFileList) return;
        const query = localSearchInput ? localSearchInput.value.toLowerCase().trim() : '';
        const files = sortedFiles(localFiles).filter(file => !query || file.name.toLowerCase().includes(query));
        if (!files.length) {
            localFileList.innerHTML = '<div class="storage-loading"><i class="material-symbols-outlined" style="font-size:48px;color:var(--text-muted)">folder_open</i><p>No items found.</p></div>';
            return;
        }
        localFileList.innerHTML = '';
        files.forEach(file => {
            const row = document.createElement('div');
            row.className = 'local-file-item';
            row.tabIndex = 0;
            row.dataset.path = file.path;
            row.innerHTML = `
                <div class="col-checkbox"><input type="checkbox" class="storage-checkbox local-item-checkbox" ${localSelectedPaths.includes(file.path) ? 'checked' : ''}></div>
                <div class="col-name"><i class="material-symbols-outlined ${getFileIconClass(file.name, file.is_dir)}">${file.is_dir ? 'folder' : 'draft'}</i><span class="col-name-text">${escapeHtml(file.name)}</span></div>
                <div class="col-size">${file.is_dir ? '--' : formatFileSize(file.size)}</div>
                <div class="col-date">${formatDate(file.mtime)}</div>
                <div class="col-actions">
                    <button class="btn btn-icon-only local-rename" title="Rename"><i class="material-symbols-outlined">drive_file_rename_outline</i></button>
                    <button class="btn btn-icon-only local-trash" title="Move to Trash"><i class="material-symbols-outlined">delete</i></button>
                </div>`;
            row.addEventListener('dblclick', () => { if (file.is_dir) loadLocalDirectory(file.path); });
            row.addEventListener('click', () => {
                activeFilePane = 'local';
                localFocusedPath = file.path;
                document.querySelectorAll('.local-file-item.active-focus').forEach(item => item.classList.remove('active-focus'));
                row.classList.add('active-focus');
                row.focus();
            });
            const checkbox = row.querySelector('.local-item-checkbox');
            checkbox.addEventListener('click', e => e.stopPropagation());
            checkbox.addEventListener('change', () => {
                if (checkbox.checked && !localSelectedPaths.includes(file.path)) localSelectedPaths.push(file.path);
                if (!checkbox.checked) localSelectedPaths = localSelectedPaths.filter(path => path !== file.path);
            });
            row.querySelector('.local-rename').addEventListener('click', async e => {
                e.stopPropagation();
                const name = prompt('Rename item:', file.name);
                if (!name || name.trim() === file.name) return;
                const res = await postJson('/api/files/local/rename', { path: file.path, name: name.trim() });
                showToast(res.message || (res.success ? 'Item renamed' : 'Rename failed'), res.success ? 'success' : 'error');
                if (res.success) loadLocalDirectory(currentLocalPath);
            });
            row.querySelector('.local-trash').addEventListener('click', async e => {
                e.stopPropagation();
                if (!confirm(`Move "${file.name}" to Trash?`)) return;
                const res = await postJson('/api/files/local/trash', { path: file.path });
                showToast(res.message || (res.success ? 'Moved to Trash' : 'Move failed'), res.success ? 'success' : 'error');
                if (res.success) loadLocalDirectory(currentLocalPath);
            });
            localFileList.appendChild(row);
        });
    }

    async function loadLocalDirectory(path, soft = false) {
        if (!path) return;
        if (!soft && localFileList) localFileList.innerHTML = '<div class="storage-loading"><div class="spinner"></div><p>Loading Mac files…</p></div>';
        try {
            const hidden = filesShowHidden && filesShowHidden.checked ? '1' : '0';
            const response = await fetch(`${API_BASE}/api/files/local?path=${encodeURIComponent(path)}&show_hidden=${hidden}`);
            const data = await response.json();
            if (!data.success) throw new Error(data.message || 'Could not open local folder');
            currentLocalPath = data.path;
            localFiles = data.files || [];
            localSelectedPaths = localSelectedPaths.filter(selected => localFiles.some(file => file.path === selected));
            if (localCurrentPath) localCurrentPath.value = currentLocalPath;
            if (localSelectAll) localSelectAll.checked = false;
            renderLocalFiles();
        } catch (err) {
            if (localFileList) localFileList.innerHTML = `<div class="storage-loading"><i class="material-symbols-outlined">error</i><p>${escapeHtml(err.message)}</p></div>`;
        }
    }

    async function loadLocalRoots() {
        if (!localRootSelect) return;
        try {
            const response = await fetch(`${API_BASE}/api/files/roots`);
            const data = await response.json();
            if (!data.success || !data.roots.length) return;
            localRootSelect.innerHTML = data.roots.map(root => `<option value="${escapeHtml(root.path)}">${escapeHtml(root.name)}</option>`).join('');
            const preferred = data.roots.find(root => root.name === 'Downloads') || data.roots[0];
            localRootSelect.value = preferred.path;
            await loadLocalDirectory(preferred.path);
        } catch (err) {
            showToast(`Could not load Mac folders: ${err.message}`, 'error');
        }
    }

    async function loadPhoneStorages() {
        if (!window.isConnected || !phoneStorageSelect) return;
        try {
            const response = await fetch(`${API_BASE}/api/files/storages`);
            const data = await response.json();
            if (!data.success || !data.storages.length) return;
            phoneStorageSelect.innerHTML = data.storages.map(storage => `<option value="${escapeHtml(storage.path)}">${escapeHtml(storage.name)}</option>`).join('');
            const current = data.storages.find(storage => window.currentStoragePath && window.currentStoragePath.startsWith(storage.path));
            phoneStorageSelect.value = current ? current.path : data.storages[0].path;
        } catch (err) {
            console.error('Could not discover phone storage:', err);
        }
    }

    async function queueTransfer(direction) {
        const localToPhone = direction === 'local_to_phone';
        const paths = localToPhone ? localSelectedPaths : selectedPaths;
        const source = localToPhone ? localFiles : allFetchedFiles;
        const destination = localToPhone ? window.currentStoragePath : currentLocalPath;
        const items = source.filter(file => paths.includes(file.path)).map(file => ({ path: file.path, size: file.size, is_dir: file.is_dir }));
        if (!items.length) {
            showToast(`Select one or more ${localToPhone ? 'Mac' : 'phone'} items first.`, 'info');
            return;
        }
        const res = await postJson('/api/transfers/start', { direction, items, destination, conflict: transferConflictPolicy ? transferConflictPolicy.value : 'rename' });
        showToast(res.message || (res.success ? 'Transfer queued' : 'Transfer failed'), res.success ? 'success' : 'error');
        if (res.success) pollTransfers();
    }

    async function cancelTransfer(id) {
        const res = await postJson('/api/transfers/cancel', { id });
        showToast(res.message, res.success ? 'info' : 'error');
        pollTransfers();
    }

    async function pollTransfers() {
        if (!transferQueueList || !transferQueueSummary) return;
        try {
            const response = await fetch(`${API_BASE}/api/transfers`);
            const data = await response.json();
            const jobs = data.jobs || [];
            const active = jobs.filter(job => ['queued', 'running'].includes(job.status));
            hasActiveTransfers = active.length > 0;
            transferQueueSummary.innerText = active.length ? `${active.length} active` : (jobs.length ? `${jobs.length} recent` : 'No transfers');
            if (!jobs.length) {
                hasActiveTransfers = false;
                transferQueueList.innerHTML = '<div class="transfer-empty">Select items in either pane and use the transfer arrows.</div>';
                return;
            }
            transferQueueList.innerHTML = '';
            jobs.slice(0, 8).forEach(job => {
                const direction = job.direction === 'local_to_phone' ? 'Mac → Phone' : 'Phone → Mac';
                const errors = job.errors && job.errors.length ? `<div class="transfer-error">${escapeHtml(job.errors[0])}</div>` : '';
                const item = document.createElement('div');
                item.className = `transfer-job transfer-${job.status}`;
                item.innerHTML = `<div class="transfer-job-top"><strong>${direction}</strong><span>${escapeHtml(job.active_name || `${job.total_items} item(s)`)}</span><em>${escapeHtml(job.status)}</em>${['queued', 'running'].includes(job.status) ? '<button class="btn btn-sm btn-ghost">Cancel</button>' : ''}</div><div class="progress-bar-track"><div class="progress-bar-fill" style="width:${Math.max(0, Math.min(100, job.progress || job.active_progress || 0))}%"></div></div>${errors}`;
                const cancel = item.querySelector('button');
                if (cancel) cancel.addEventListener('click', () => cancelTransfer(job.id));
                transferQueueList.appendChild(item);
                if (['completed', 'failed'].includes(job.status) && !refreshedTransferIds.has(job.id)) {
                    refreshedTransferIds.add(job.id);
                    if (currentLocalPath) loadLocalDirectory(currentLocalPath, true);
                    if (window.isConnected && window.currentStoragePath) window.loadStorageDirectory(window.currentStoragePath, true);
                }
            });
        } catch (err) {
            console.error('Transfer queue polling failed:', err);
        }
    }

    if (localRootSelect) localRootSelect.addEventListener('change', () => loadLocalDirectory(localRootSelect.value));
    if (phoneStorageSelect) phoneStorageSelect.addEventListener('change', () => window.loadStorageDirectory(phoneStorageSelect.value));
    if (localBtnBack) localBtnBack.addEventListener('click', () => loadLocalDirectory(localParent(currentLocalPath)));
    if (localBtnRefresh) localBtnRefresh.addEventListener('click', () => loadLocalDirectory(currentLocalPath));
    if (localCurrentPath) localCurrentPath.addEventListener('keydown', e => { if (e.key === 'Enter') loadLocalDirectory(localCurrentPath.value.trim()); });
    if (localSearchInput) localSearchInput.addEventListener('input', renderLocalFiles);
    if (localBtnNewFolder) localBtnNewFolder.addEventListener('click', async () => {
        const name = prompt('Enter new folder name:');
        if (!name) return;
        const res = await postJson('/api/files/local/mkdir', { parent: currentLocalPath, name: name.trim() });
        showToast(res.message || (res.success ? 'Folder created' : 'Could not create folder'), res.success ? 'success' : 'error');
        if (res.success) loadLocalDirectory(currentLocalPath);
    });
    if (localSelectAll) localSelectAll.addEventListener('change', () => {
        const query = localSearchInput ? localSearchInput.value.toLowerCase().trim() : '';
        const visible = sortedFiles(localFiles).filter(file => !query || file.name.toLowerCase().includes(query));
        localSelectedPaths = localSelectAll.checked ? visible.map(file => file.path) : [];
        renderLocalFiles();
    });
    if (transferToPhone) transferToPhone.addEventListener('click', () => queueTransfer('local_to_phone'));
    if (transferToMac) transferToMac.addEventListener('click', () => queueTransfer('phone_to_local'));
    if (filesShowHidden) filesShowHidden.addEventListener('change', () => {
        if (currentLocalPath) loadLocalDirectory(currentLocalPath);
        if (window.isConnected && window.currentStoragePath) window.loadStorageDirectory(window.currentStoragePath);
    });
    if (filesSortMode) filesSortMode.addEventListener('change', () => {
        allFetchedFiles = sortedFiles(allFetchedFiles);
        renderFileList(allFetchedFiles);
        renderLocalFiles();
    });

    loadLocalRoots();
    loadPhoneStorages();
    pollTransfers();
    setInterval(pollTransfers, 1000);

    // Storage auto-refresh loop (Real-time storage updates every 4 seconds)
    setInterval(() => {
        const activeTabButton = document.querySelector('.nav-btn.active');
        const activeTab = activeTabButton ? activeTabButton.getAttribute('data-tab') : '';
        if (activeTab === 'storage' && window.currentStoragePath && window.isConnected && !hasActiveTransfers) {
            const searchInput = document.getElementById('storage-search-input');
            const pathInput = document.getElementById('storage-path-input');
            const galleryModal = document.getElementById('gallery-modal');
            const isPreviewActive = galleryModal && galleryModal.classList.contains('active');
            
            // Do not refresh if user is typing, inputting or previewing
            if (document.activeElement === searchInput || document.activeElement === pathInput || isPreviewActive) {
                return;
            }
            
            // Also do not refresh if they are currently drag-overing
            if (storageDropOverlay && storageDropOverlay.classList.contains('active')) {
                return;
            }
            
            // Soft refresh: fetch the directory content, maintaining selection/focus state
            window.loadStorageDirectory(window.currentStoragePath, true);
        }
    }, 4000);
});
