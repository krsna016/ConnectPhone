document.addEventListener('DOMContentLoaded', () => {
    // API base URL (detect file:// protocol to point to local Python server)
    const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8282' : '';
    
    // State variables
    let currentTab = 'connection';
    let statusInterval = null;
    let scrcpyWasRunning = false;
    let isRecording = false;

    // DOM Elements
    const navButtons = document.querySelectorAll('.nav-btn');
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

        // Toggle Phone Call Status polling
        if (tabName === 'calls') {
            startCallStatusPolling();
        } else {
            stopCallStatusPolling();
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
        
        let icon = '🔔';
        if (type === 'success') icon = '✅';
        else if (type === 'error') icon = '❌';
        else if (type === 'info') icon = '⏳';
        
        toast.innerHTML = `
            <span class="toast-icon">${icon}</span>
            <span class="toast-message">${message}</span>
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
        } catch (err) {
            console.error("Failed to query API status:", err);
            if (connStatus) connStatus.className = 'connection-badge server-offline';
            if (connStatusText) connStatusText.textContent = 'Server Offline';
            if (headerDevice) headerDevice.textContent = 'Cannot reach Python API server. Please run ConnectPhoneUI.app or start ConnectPhoneUI.py in terminal.';
        }
    }

    function updateConnectionUI(data) {
        const cleanInfo = (data.device_info || "").replace(/\\033\[[0-9;]*m/g, '').replace(/\x1b\[[0-9;]*m/g, '');
        if (data.connected) {
            connStatus.className = 'connection-badge connected';
            connStatusText.textContent = 'Connected';
            headerDevice.textContent = cleanInfo || "Connected Device";
            if (btnHeaderUnlock) btnHeaderUnlock.style.display = 'inline-flex';
            if (btnPhoneUnlock) btnPhoneUnlock.disabled = false;
        } else {
            connStatus.className = 'connection-badge disconnected';
            connStatusText.textContent = 'Disconnected';
            headerDevice.textContent = 'Connection Center 🔗 Connect using USB or Wi-Fi IP';
            if (btnHeaderUnlock) btnHeaderUnlock.style.display = 'none';
            if (btnPhoneUnlock) btnPhoneUnlock.disabled = true;
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
                            
                            activeDetailsBox.innerHTML = `
                                <div class="active-device-details">
                                    <div class="detail-item">
                                        <span>📱 Device Model</span>
                                        <p>${model}</p>
                                    </div>
                                    <div class="detail-item">
                                        <span>🔋 Battery Level</span>
                                        <p>${battery}</p>
                                    </div>
                                    <div class="detail-item">
                                        <span>💾 Available Storage</span>
                                        <p>${storage}</p>
                                    </div>
                                    <div class="detail-item">
                                        <span>🌐 IP Address / Serial</span>
                                        <p>${data.devices[0] || 'USB Connection'}</p>
                                    </div>
                                </div>
                            `;
                        } else {
                            activeDetailsBox.innerHTML = `<p class="status-placeholder">${cleanInfo}</p>`;
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
                    const icon = isWireless ? '📶' : '🔌';
                    const statusText = device.status === 'device' ? 'online' : (device.status === 'unauthorized' ? 'unauthorized' : 'offline');
                    
                    row.innerHTML = `
                        <div class="device-info-left">
                            <span class="device-type-icon">${icon}</span>
                            <div class="device-meta">
                                <h4>${device.model}</h4>
                                <p>${device.serial} (${isWireless ? 'Wi-Fi' : 'USB'})</p>
                            </div>
                        </div>
                        <div class="device-info-right">
                            <span class="status-badge ${statusText}">${statusText}</span>
                            ${isWireless ? `<button class="btn btn-sm btn-danger btn-device-disconnect" data-serial="${device.serial}">Disconnect</button>` : ''}
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
                    overlayRecord.textContent = '🔴';
                    cameraOverlayDesc.textContent = 'RECORDING LIVE HD VIDEO (Saving to Desktop)...';
                } else {
                    overlayRecord.classList.remove('recording');
                    overlayRecord.textContent = '🎥';
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
                overlayRecord.textContent = '🎥';
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
        if (preferencesLoaded || !config) return;
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
        

        
        document.getElementById('pref-audio-buffer').value = config.audio_buffer || '100';
        
        document.getElementById('pref-mirror-enabled').checked = config.mirror_enabled !== false;
        document.getElementById('pref-screen-off').checked = config.screen_off_enabled === true;
        document.getElementById('pref-stay-awake').checked = config.stay_awake_enabled !== false;
        document.getElementById('pref-show-touches').checked = config.show_touches_enabled === true;
        document.getElementById('pref-biometric-daemon').checked = config.biometric_daemon_enabled === true;

        // Auto fill IP in connection form
        if (config.last_ip) {
            document.getElementById('conn-ip').value = config.last_ip;
        }
    }

    // Post to endpoint helper
    async function postAction(url, bodyData = {}) {
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
            fetchStatus();
            return data;
        } catch (err) {
            let errorMsg = err.message;
            if (err.message === 'Failed to fetch' || err.name === 'TypeError') {
                errorMsg = 'Cannot reach Python API server. Please run ConnectPhoneUI.app or start ConnectPhoneUI.py in terminal.';
            }
            showToast(`Error: ${errorMsg}`, 'error');
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
    // Settings Connections Bindings
    const btnAutoConnect = document.getElementById('btn-conn-autoconnect');
    if (btnAutoConnect) {
        btnAutoConnect.addEventListener('click', () => {
            showToast('Auto-scanning ports & connecting...', 'info');
            postAction('/api/connect/auto');
        });
    }

    document.getElementById('btn-conn-connect').addEventListener('click', () => {
        const ip = document.getElementById('conn-ip').value.trim();
        const port = document.getElementById('conn-port').value.trim();
        if (!ip) {
            showToast('IP address is required.', 'error');
            return;
        }
        showToast(`Connecting to ${ip}:${port}...`, 'info');
        postAction('/api/connect', { ip: ip, port: port });
    });

    document.getElementById('btn-conn-pair').addEventListener('click', () => {
        const ip = document.getElementById('conn-ip').value.trim();
        const port = document.getElementById('pair-port').value.trim();
        const code = document.getElementById('pair-code').value.trim();
        if (!ip || !port || !code) {
            showToast('IP, Pairing Port, and Pairing Code are all required.', 'error');
            return;
        }
        showToast('Pairing wirelessly with device...', 'info');
        postAction('/api/pair', { ip: ip, port: port, code: code });
    });

    document.getElementById('btn-disconnect-all').addEventListener('click', () => {
        postAction('/api/disconnect');
    });

    document.getElementById('btn-restart-adb').addEventListener('click', () => {
        showToast('Restarting ADB server...', 'info');
        postAction('/api/restart_adb');
    });

    // mDNS Auto-Discovery Bindings
    const btnScanMdns = document.getElementById('btn-scan-mdns-devices');
    const mdnsList = document.getElementById('mdns-discovered-list');
    if (btnScanMdns && mdnsList) {
        btnScanMdns.addEventListener('click', async () => {
            mdnsList.innerHTML = '<p class="list-placeholder">⚡ Scanning Wi-Fi network for Wireless Debugging services (takes 2 seconds)...</p>';
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
                                <span class="device-type-icon">🔍</span>
                                <div class="device-meta">
                                    <h4>${service.name} (${isPairing ? 'Pairing Service' : 'Connect Target'})</h4>
                                    <p>${service.ip}:${service.port}</p>
                                </div>
                            </div>
                            <div class="device-info-right">
                                <button class="btn btn-sm btn-primary btn-mdns-action" data-ip="${service.ip}" data-port="${service.port}" data-type="${service.type}">
                                    ${isPairing ? '🔑 Start Pairing' : '⚡ Connect'}
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
                                postAction('/api/connect', { ip: service.ip, port: service.port });
                            }
                        });
                        
                        mdnsList.appendChild(row);
                    });
                } else {
                    mdnsList.innerHTML = '<p class="list-placeholder">No active wireless debugging services discovered on local network. Verify "Wireless Debugging" is toggled ON in Developer Options.</p>';
                }
            } catch (err) {
                console.error("mDNS scan error:", err);
                mdnsList.innerHTML = `<p class="list-placeholder error">Scan failed: ${err.message}</p>`;
            } finally {
                btnScanMdns.disabled = false;
            }
        });
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
            android_pin: document.getElementById('pref-pin').value.trim(),
            applock_pin: document.getElementById('pref-applock').value.trim(),
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
            pingResult.textContent = '⚡ Running ping test... please wait...';
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

    // Phone Call Manager functionality
    let callStateInterval = null;

    function startCallStatusPolling() {
        if (callStateInterval) clearInterval(callStateInterval);
        pollCallStatus();
        callStateInterval = setInterval(pollCallStatus, 1500);
    }

    function stopCallStatusPolling() {
        if (callStateInterval) {
            clearInterval(callStateInterval);
            callStateInterval = null;
        }
    }

    async function pollCallStatus() {
        try {
            const res = await fetch(`${API_BASE}/api/device/call_state`);
            if (!res.ok) throw new Error("Failed to fetch call state");
            const data = await res.json();
            
            const dot = document.getElementById('call-status-dot');
            const txt = document.getElementById('call-status-text');
            const sub = document.getElementById('call-status-sub');
            
            if (dot && txt && sub) {
                dot.className = 'status-dot-large';
                
                if (data.state === 'ringing') {
                    dot.classList.add('ringing');
                    txt.textContent = data.message || 'Incoming Call...';
                    sub.textContent = data.sub || 'Someone is calling your phone';
                } else if (data.state === 'active') {
                    dot.classList.add('active-call');
                    txt.textContent = data.message || 'Active Call';
                    sub.textContent = data.sub || 'Ongoing phone conversation';
                } else {
                    dot.classList.add('idle');
                    txt.textContent = data.message || 'Line Idle';
                    sub.textContent = data.sub || 'No active call detected';
                }
            }
        } catch (err) {
            console.error("Error polling call status:", err);
        }
    }

    // Set up dialer key listeners
    const dialNumberInput = document.getElementById('dial-number');
    const dialBtns = document.querySelectorAll('.dial-btn');
    const btnDialClear = document.getElementById('btn-dial-clear');
    const btnDialCall = document.getElementById('btn-dial-call');

    if (dialBtns) {
        dialBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                if (dialNumberInput) {
                    dialNumberInput.value += btn.getAttribute('data-val');
                }
            });
        });
    }

    if (btnDialClear) {
        btnDialClear.addEventListener('click', () => {
            if (dialNumberInput) {
                dialNumberInput.value = '';
            }
        });
    }

    if (btnDialCall) {
        btnDialCall.addEventListener('click', () => {
            if (dialNumberInput) {
                const number = dialNumberInput.value.trim();
                if (!number) {
                    showToast("Please enter a phone number to call", "error");
                    return;
                }
                showToast(`Dialing ${number}...`, 'info');
                postAction('/api/device/call', { number: number });
            }
        });
    }

    // Call Action Listeners
    const btnCallAnswer = document.getElementById('btn-call-answer');
    const btnCallHangup = document.getElementById('btn-call-hangup');

    if (btnCallAnswer) {
        btnCallAnswer.addEventListener('click', () => {
            showToast("Answering incoming call...", "info");
            postAction('/api/device/call/answer');
        });
    }

    if (btnCallHangup) {
        btnCallHangup.addEventListener('click', () => {
            showToast("Ending call / rejecting...", "info");
            postAction('/api/device/call/hangup');
        });
    }

    // Touch ID Unlock Button Listeners
    if (btnHeaderUnlock) {
        btnHeaderUnlock.addEventListener('click', () => {
            showToast("Prompting Touch ID on Mac to unlock phone...", "info");
            postAction('/api/device/unlock');
        });
    }

    if (btnPhoneUnlock) {
        btnPhoneUnlock.addEventListener('click', () => {
            showToast("Prompting Touch ID on Mac to unlock phone...", "info");
            postAction('/api/device/unlock');
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
        fetchStatus();
        statusInterval = setInterval(fetchStatus, 2000);
    }
    
    initDashboard();
});
