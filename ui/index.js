document.addEventListener('DOMContentLoaded', () => {
    // API base URL (detect file:// protocol to point to local Python server)
    const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8282' : '';
    
    // State variables
    let currentTab = 'mirroring';
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
            updateSyncWatcherUI(data.sync_watcher_active);
            
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
        if (data.connected) {
            connStatus.className = 'connection-badge connected';
            connStatusText.textContent = 'Connected';
            // Strip ANSI codes if returned in raw string
            const cleanInfo = (data.device_info || "Connected Device").replace(/\\033\[[0-9;]*m/g, '').replace(/\x1b\[[0-9;]*m/g, '');
            headerDevice.textContent = cleanInfo;
        } else {
            connStatus.className = 'connection-badge disconnected';
            connStatusText.textContent = 'Disconnected';
            headerDevice.textContent = 'Select Connection Settings in Preferences or connect using Wi-Fi IP';
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

    function updateSyncWatcherUI(isActive) {
        const syncIndicator = document.getElementById('sync-indicator');
        const syncLabel = document.getElementById('sync-label');
        const btnToggle = document.getElementById('btn-file-sync-toggle');

        if (isActive) {
            syncIndicator.className = 'sync-status-indicator active';
            syncLabel.textContent = 'Folder Sync: Active';
            btnToggle.textContent = 'Stop Sync Watcher';
            btnToggle.className = 'btn btn-danger';
        } else {
            syncIndicator.className = 'sync-status-indicator';
            syncLabel.textContent = 'Folder Sync: Inactive';
            btnToggle.textContent = 'Start Sync Watcher';
            btnToggle.className = 'btn btn-accent';
        }
    }

    function showBrandPanel(brand) {
        document.querySelectorAll('.brand-panel').forEach(p => p.classList.add('hidden'));
        const panel = document.getElementById(`panel-brand-${brand}`);
        if (panel) {
            panel.classList.remove('hidden');
        }
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
        
        const activeBrand = config.device_profile || 'generic';
        const brandSelect = document.getElementById('profile-brand-select');
        if (brandSelect) {
            brandSelect.value = activeBrand;
        }

        // Mark active badge on matching nav item
        document.querySelectorAll('.profile-nav-item').forEach(item => {
            if (item.getAttribute('data-brand') === activeBrand) {
                item.classList.add('active-profile');
            } else {
                item.classList.remove('active-profile');
            }
        });

        // Update Apply buttons styling
        document.querySelectorAll('.btn-apply-profile').forEach(btn => {
            const btnBrand = btn.getAttribute('data-brand');
            if (btnBrand === activeBrand) {
                btn.textContent = '✓ Profile Applied';
                btn.classList.add('applied-profile');
                btn.classList.remove('btn-primary');
            } else {
                const brandPretty = btnBrand === 'generic' ? 'Generic' :
                                    btnBrand === 'nothing' ? 'Nothing Phone' :
                                    btnBrand === 'xiaomi' ? 'Xiaomi' :
                                    btnBrand === 'samsung' ? 'Samsung' : 'OnePlus';
                btn.textContent = `Apply ${brandPretty} Profile`;
                btn.classList.remove('applied-profile');
                btn.classList.add('btn-primary');
            }
        });

        // Default selection to active profile if nothing is selected
        const currentSelected = document.querySelector('.profile-nav-item.selected');
        if (!currentSelected) {
            const activeNavItem = document.querySelector(`.profile-nav-item[data-brand="${activeBrand}"]`);
            if (activeNavItem) {
                activeNavItem.classList.add('selected');
            }
            showBrandPanel(activeBrand);
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

    // Control Keypad Click Bindings
    const bindControl = (btnId, actionName) => {
        const el = document.getElementById(btnId);
        if (el) {
            el.addEventListener('click', () => {
                postAction('/api/control', { action: actionName });
            });
        }
    };
    
    bindControl('ctrl-power', 'power');
    bindControl('ctrl-vol-up', 'vol_up');
    bindControl('ctrl-vol-down', 'vol_down');
    bindControl('ctrl-back', 'back');
    bindControl('ctrl-home', 'home');
    bindControl('ctrl-recents', 'recents');
    bindControl('ctrl-settings', 'settings');
    bindControl('ctrl-mute', 'mute');
    bindControl('ctrl-media-play', 'play_pause');
    bindControl('ctrl-media-prev', 'prev_track');
    bindControl('ctrl-media-next', 'next_track');
    bindControl('ctrl-backspace', 'backspace');
    bindControl('ctrl-enter', 'enter');
    bindControl('ctrl-tab', 'tab');

    // Touch ID Unlock Simulation
    document.getElementById('ctrl-touch-id').addEventListener('click', () => {
        showToast('Confirm Touch ID on your Mac...', 'info');
        postAction('/api/touch_id_unlock');
    });

    // Simulate Keyboard Input Typer
    const textTyperInput = document.getElementById('type-text-input');
    document.getElementById('btn-type-send').addEventListener('click', () => {
        const text = textTyperInput.value.trim();
        if (!text) {
            showToast('Please type some text first.', 'error');
            return;
        }
        postAction('/api/type', { text: text });
        textTyperInput.value = '';
    });

    // File Dispatch & Photo Pulling Clicks
    document.getElementById('btn-file-push').addEventListener('click', () => {
        const path = document.getElementById('push-path-input').value.trim();
        if (!path) {
            showToast('Mac filepath is required.', 'error');
            return;
        }
        showToast('Pushing file via ADB...', 'info');
        postAction('/api/files/push', { mac_path: path });
    });

    document.getElementById('btn-file-pull').addEventListener('click', () => {
        showToast('Searching and pulling latest photo...', 'info');
        postAction('/api/files/pull_photo');
    });

    document.getElementById('btn-file-sync-toggle').addEventListener('click', () => {
        postAction('/api/files/sync_watcher/toggle');
    });

    // Settings Connections Bindings
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
    });

    // Device Profiles Navigation Click Bindings
    document.querySelectorAll('.profile-nav-item').forEach(item => {
        item.addEventListener('click', () => {
            // Remove selected class from all nav items
            document.querySelectorAll('.profile-nav-item').forEach(i => i.classList.remove('selected'));
            
            // Add selected class to current clicked nav item
            item.classList.add('selected');
            
            const brand = item.getAttribute('data-brand');
            
            // Update hidden select dropdown
            const brandSelect = document.getElementById('profile-brand-select');
            if (brandSelect) {
                brandSelect.value = brand;
            }
            
            showBrandPanel(brand);
        });
    });

    // Apply Profile Button Clicks
    document.querySelectorAll('.btn-apply-profile').forEach(btn => {
        btn.addEventListener('click', () => {
            const selectedBrand = btn.getAttribute('data-brand');
            if (selectedBrand === 'nothing') {
                const fpsInput = document.getElementById('pref-fps');
                if (fpsInput) fpsInput.value = '120';
            }
            showToast(`Applying ${selectedBrand.toUpperCase()} profile...`, 'info');
            postAction('/api/settings/save', {
                device_profile: selectedBrand,
                camera_fps: selectedBrand === 'nothing' ? '120' : undefined
            }).then(() => {
                preferencesLoaded = false;
                fetchStatus();
            });
        });
    });

    const bindGlyphAction = (btnId, endpoint, body = {}) => {
        const el = document.getElementById(btnId);
        if (el) {
            el.addEventListener('click', () => {
                showToast('Sending Glyph command...', 'info');
                postAction(endpoint, body);
            });
        }
    };

    bindGlyphAction('btn-nothing-glyph-on', '/api/nothing/glyph/toggle', { enabled: true });
    bindGlyphAction('btn-nothing-glyph-off', '/api/nothing/glyph/toggle', { enabled: false });
    bindGlyphAction('btn-nothing-glyph-settings', '/api/nothing/glyph/settings');
    bindGlyphAction('btn-nothing-glyph-flash', '/api/nothing/glyph/flash');

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
            pingResult.textContent = '⚡ Running ping test... please wait...';
            pingResult.className = 'ping-result running';
            try {
                // Post endpoint
                const res = await postAction('/api/ping');
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
