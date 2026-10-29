/**
 * Minimal Edge Camera Node - Client Controller
 * Focuses purely on edge camera operations:
 *   1. Camera Viewport with YuNet + SFace overlays
 *   2. Node Settings (Camera ID, Location, Lens)
 *   3. Connected Peer Nodes list
 *   4. Deduplicated Student Sighting Log (verifies every frame, but displays only once)
 *   5. Data Sent Logs (Outbound transmission terminal)
 */

(function () {
  'use strict';

  // DOM Elements - Camera
  const video = document.getElementById('webcamVideo');
  const canvas = document.getElementById('outputCanvas');
  const ctx = canvas.getContext('2d');
  const standbyOverlay = document.getElementById('standbyOverlay');
  const btnStartOverlay = document.getElementById('btnStartOverlay');
  const btnToggleCamera = document.getElementById('btnToggleCamera');
  const btnCameraText = document.getElementById('btnCameraText');
  const btnEnrollModal = document.getElementById('btnEnrollModal');
  const btnSnapshot = document.getElementById('btnSnapshot');
  const cameraSelect = document.getElementById('cameraSelect');

  // Metrics
  const metricFps = document.getElementById('metricFps');
  const metricLatency = document.getElementById('metricLatency');
  const metricFaces = document.getElementById('metricFaces');
  const metricEnrolled = document.getElementById('metricEnrolled');

  // Node Identity Inputs & Badges
  const headerNodeBadge = document.getElementById('headerNodeBadge');
  const inputCameraId = document.getElementById('inputCameraId');
  const inputLocation = document.getElementById('inputLocation');
  const btnApplyConfig = document.getElementById('btnApplyConfig');
  const centralLinkStatus = document.getElementById('centralLinkStatus');

  // Connected Nodes
  const connectedNodesList = document.getElementById('connectedNodesList');
  const connectedCountBadge = document.getElementById('connectedCountBadge');
  const sidebarCurrentNode = document.getElementById('sidebarCurrentNode');
  const sidebarCurrentLoc = document.getElementById('sidebarCurrentLoc');

  // Deduplicated Student Sightings Log
  const studentCountBadge = document.getElementById('studentCountBadge');
  const studentTableBody = document.getElementById('studentTableBody');
  const eventBanner = document.getElementById('eventBanner');
  const bannerDesc = document.getElementById('bannerDesc');

  // Data Sent Logs (Terminal)
  const dataSentTerminal = document.getElementById('dataSentTerminal');

  // Modal Elements
  const enrollModal = document.getElementById('enrollModal');
  const btnCloseModal = document.getElementById('btnCloseModal');
  const btnCancelEnroll = document.getElementById('btnCancelEnroll');
  const btnSubmitEnroll = document.getElementById('btnSubmitEnroll');
  const enrollCanvas = document.getElementById('enrollCanvas');
  const enrollStudentId = document.getElementById('enrollStudentId');
  const enrollName = document.getElementById('enrollName');
  const enrollDept = document.getElementById('enrollDept');
  const enrollBatch = document.getElementById('enrollBatch');
  const enrollError = document.getElementById('enrollError');

  // Offscreen Canvas
  const captureCanvas = document.createElement('canvas');
  const captureCtx = captureCanvas.getContext('2d', { willReadFrequently: true });

  // State
  let stream = null;
  let isRunning = false;
  let ws = null;
  let wsConnected = false;
  let isSendingFrame = false;
  let frameTimer = null;
  let bannerTimer = null;

  // Deduplicated student sightings map: student_id -> { id, name, verifications, firstSeen, lastConfirmed, status, confidence }
  const verifiedStudentsMap = new Map();

  // ---------------------------------------------------------------------------
  // 1. Insecure Context & HTTPS Detection
  // ---------------------------------------------------------------------------
  function getSafeMediaDevices() {
    if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function') {
      return navigator.mediaDevices;
    }
    const legacyGUM = navigator.getUserMedia ||
                      navigator.webkitGetUserMedia ||
                      navigator.mozGetUserMedia ||
                      navigator.msGetUserMedia;
    if (legacyGUM) {
      return {
        getUserMedia: function (constraints) {
          return new Promise((resolve, reject) => {
            legacyGUM.call(navigator, constraints, resolve, reject);
          });
        },
        enumerateDevices: async function () { return []; }
      };
    }
    return null;
  }

  function checkSecureContext() {
    const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    const isHttps = window.location.protocol === 'https:';
    if (!isHttps && !isLocal) {
      showInsecureHttpsWarning();
      return false;
    }
    return true;
  }

  function showInsecureHttpsWarning() {
    const targetUrl = `https://${window.location.hostname}:8443${window.location.pathname}`;
    const box = document.getElementById('standbyBox');
    if (!box) return;

    box.innerHTML = `
      <div class="standby-icon">🔒</div>
      <h3 style="color:#00e5ff;">HTTPS Required for Mobile Cameras</h3>
      <p style="font-size:13px; line-height: 1.6; color:#cbd5e1; margin-bottom: 18px;">
        Mobile browsers (Chrome & Safari on Android/iOS) strictly block camera hardware on remote IP addresses unless accessed over <strong>HTTPS</strong>.
      </p>
      <a href="${targetUrl}" class="btn btn-primary btn-lg" style="text-decoration:none; display:inline-flex; margin-bottom:14px;">
        🛡️ Open in Secure HTTPS Mode (:8443)
      </a>
      <div style="font-size: 11px; color:#94a3b8; line-height: 1.4; background: rgba(255,255,255,0.05); padding: 8px 12px; border-radius: 6px;">
        <em>Note:</em> When the self-signed SSL warning appears, tap <strong>"Advanced" ➔ "Proceed to ${window.location.hostname} (unsafe)"</strong> to activate your camera.
      </div>
    `;
    standbyOverlay.classList.remove('hidden');
  }

  // ---------------------------------------------------------------------------
  // 2. Hardware Camera Management
  // ---------------------------------------------------------------------------
  async function enumerateCameras() {
    const md = getSafeMediaDevices();
    if (!md || !md.enumerateDevices) return;
    try {
      const devices = await md.enumerateDevices();
      const videoDevs = devices.filter(d => d.kind === 'videoinput');

      cameraSelect.innerHTML = '';
      if (videoDevs.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'Default Camera (Auto)';
        cameraSelect.appendChild(opt);
        return;
      }

      videoDevs.forEach((d, idx) => {
        const opt = document.createElement('option');
        opt.value = d.deviceId;
        opt.textContent = d.label || `Lens ${idx + 1} (${d.deviceId.slice(0, 5)}...)`;
        cameraSelect.appendChild(opt);
      });
    } catch (e) {
      console.warn('Error discovering cameras:', e);
    }
  }

  let lastRenderedServerFrame = false;

  async function startCameraNode() {
    const md = getSafeMediaDevices();
    if (!md) {
      if (!window.isSecureContext && window.location.protocol !== 'https:') {
        showInsecureHttpsWarning();
        return;
      }
      alert('Camera Error: Browser does not support mediaDevices or camera access is blocked in this context.');
      return;
    }

    const deviceId = cameraSelect.value;
    let videoConstraint = {};

    if (deviceId) {
      // Hardware device specified: DO NOT include facingMode to prevent OverconstrainedError
      videoConstraint = {
        deviceId: { exact: deviceId },
        width: { ideal: 640 },
        height: { ideal: 480 }
      };
    } else {
      // Default: use facingMode as 'ideal' so desktop webcams don't fail
      videoConstraint = {
        facingMode: { ideal: 'user' },
        width: { ideal: 640 },
        height: { ideal: 480 }
      };
    }

    try {
      if (stream) {
        stream.getTracks().forEach(t => t.stop());
        stream = null;
      }

      try {
        stream = await md.getUserMedia({ video: videoConstraint, audio: false });
      } catch (constraintErr) {
        console.warn('Strict video constraints failed; falling back to basic video constraint:', constraintErr);
        // Fallback: minimal constraint that works on all cameras
        stream = await md.getUserMedia({ video: true, audio: false });
      }

      video.srcObject = stream;
      await video.play();

      // Wait for video stream to establish width/height
      if (!video.videoWidth) {
        await new Promise((resolve) => {
          video.onloadedmetadata = () => resolve();
          setTimeout(resolve, 600);
        });
      }

      isRunning = true;
      lastRenderedServerFrame = false;
      standbyOverlay.classList.add('hidden');
      btnToggleCamera.classList.remove('btn-success');
      btnToggleCamera.classList.add('btn-secondary');
      btnCameraText.textContent = 'Stop Camera';

      const w = video.videoWidth || 640;
      const h = video.videoHeight || 480;
      canvas.width = w;
      canvas.height = h;
      captureCanvas.width = w;
      captureCanvas.height = h;

      // Draw initial frame immediately to canvas so user never sees black screen
      if (video.videoWidth && video.videoHeight) {
        ctx.drawImage(video, 0, 0, w, h);
      }

      await enumerateCameras();
      connectWebSocket();
      startFrameLoop();
      logDataSent('INFO', 'Camera stream active. Edge pipeline processing frames.');
    } catch (err) {
      console.error('Failed to start camera:', err);
      let errMsg = err.message || '';

      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        errMsg = 'Camera permission denied. Please allow camera permissions in your browser address bar and refresh.';
      } else if (err.name === 'NotReadableError' || err.name === 'TrackStartError') {
        errMsg = 'Camera hardware is in use by another application or process. Please close any other app using the camera.';
      } else if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
        errMsg = 'No camera hardware found on this device.';
      } else if (err.name === 'OverconstrainedError') {
        errMsg = `Camera constraint (${err.constraint}) cannot be satisfied by your hardware.`;
      }

      if (!window.isSecureContext && window.location.protocol !== 'https:') {
        showInsecureHttpsWarning();
      } else {
        alert(`Camera Hardware Error: ${errMsg || err.name || 'Unable to open camera'}`);
      }
      stopCameraNode();
    }
  }

  function stopCameraNode() {
    isRunning = false;
    if (frameTimer) {
      clearInterval(frameTimer);
      frameTimer = null;
    }
    if (stream) {
      stream.getTracks().forEach(t => t.stop());
      stream = null;
    }
    video.srcObject = null;
    standbyOverlay.classList.remove('hidden');
    btnToggleCamera.classList.remove('btn-secondary');
    btnToggleCamera.classList.add('btn-success');
    btnCameraText.textContent = 'Start Camera Node';

    metricFps.textContent = '0.0';
    metricLatency.textContent = '0.0';
    metricFaces.textContent = '0';

    if (ws) {
      ws.close();
      ws = null;
    }
    logDataSent('WARN', 'Camera node stopped by user.');
  }

  // ---------------------------------------------------------------------------
  // 3. WebSocket Real-Time Frame Loop & AI Processing
  // ---------------------------------------------------------------------------
  function connectWebSocket() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${window.location.host}/ws/camera-feed`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      wsConnected = true;
      centralLinkStatus.innerHTML = '<span class="chip-dot"></span><span class="chip-text">NODE STREAM: ACTIVE</span>';
      centralLinkStatus.style.borderColor = 'rgba(0, 230, 118, 0.4)';
      centralLinkStatus.style.color = '#00e676';
      sendNodeConfig();
      logDataSent('SUCCESS', `Connected to Web Camera Node Pipeline: ${wsUrl}`);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleServerMessage(msg);
      } catch (e) {
        console.error('Error in WS message:', e);
      } finally {
        isSendingFrame = false;
      }
    };

    ws.onclose = () => {
      wsConnected = false;
      centralLinkStatus.innerHTML = '<span class="chip-dot"></span><span class="chip-text">NODE STREAM: OFFLINE</span>';
      centralLinkStatus.style.borderColor = 'rgba(255, 61, 0, 0.4)';
      centralLinkStatus.style.color = '#ff3d00';
      isSendingFrame = false;
      logDataSent('WARN', 'Disconnected from Web Camera Node. Reconnecting in 2s...');

      if (isRunning) {
        setTimeout(connectWebSocket, 2000);
      }
    };

    ws.onerror = () => {
      isSendingFrame = false;
    };
  }

  function sendNodeConfig() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const camId = inputCameraId.value.trim() || 'CAM-01';
    const loc = inputLocation.value.trim() || 'Main Entrance Gate';

    headerNodeBadge.textContent = `${camId} • ${loc}`;
    sidebarCurrentNode.textContent = `${camId} (This Node)`;
    sidebarCurrentLoc.textContent = loc;

    ws.send(JSON.stringify({
      type: 'CONFIG',
      camera_id: camId,
      camera_location: loc
    }));
  }

  function startFrameLoop() {
    if (frameTimer) clearInterval(frameTimer);
    let lastSendTimestamp = 0;

    frameTimer = setInterval(() => {
      if (!isRunning) return;

      // Ensure user sees live camera preview instantly while waiting for initial inference frames
      if (!lastRenderedServerFrame && video.videoWidth && video.videoHeight) {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      }

      if (!wsConnected) return;

      // Watchdog timeout: if no response for > 1800ms, unlock transmission
      if (isSendingFrame && (Date.now() - lastSendTimestamp > 1800)) {
        console.warn('Frame transmission watchdog timeout. Unlocking pipeline.');
        isSendingFrame = false;
      }

      if (isSendingFrame) return;

      lastSendTimestamp = Date.now();
      sendFrame();
    }, 35); // ~28-30 FPS target
  }

  function sendFrame() {
    if (!video.videoWidth || !video.videoHeight) return;

    isSendingFrame = true;
    captureCtx.drawImage(video, 0, 0, captureCanvas.width, captureCanvas.height);
    const jpegB64 = captureCanvas.toDataURL('image/jpeg', 0.72);

    const payload = {
      type: 'FRAME',
      camera_id: inputCameraId.value.trim() || 'CAM-01',
      camera_location: inputLocation.value.trim() || 'Main Entrance Gate',
      frame: jpegB64
    };

    try {
      ws.send(JSON.stringify(payload));
    } catch (e) {
      isSendingFrame = false;
    }
  }

  // ---------------------------------------------------------------------------
  // 4. Server Results & Deduplicated Student Sighting Log
  // ---------------------------------------------------------------------------
  function handleServerMessage(data) {
    if (data.type === 'EVENT_ACK') {
      logDataSent(
        'SUCCESS',
        `[ACK CONFIRMED] Central Receiver persisted Sighting [${data.event_id}] for Student [${data.student_id}] @ ${data.server_received_at}`
      );
      const badge = eventBanner.querySelector('.banner-badge');
      if (badge) {
        badge.textContent = 'ACK CONFIRMED';
        badge.style.background = 'rgba(0, 230, 118, 0.4)';
        badge.style.borderColor = '#00e676';
      }
      return;
    }

    if (data.type === 'CONFIG_ACK') {
      if (data.connected_nodes) {
        renderConnectedNodes(data.connected_nodes);
      }
      return;
    }

    if (data.type === 'FRAME_RESULT') {
      // 1. Render annotated video frame
      if (data.frame) {
        const img = new Image();
        img.onload = () => {
          if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
          }
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          lastRenderedServerFrame = true;
        };
        img.src = data.frame;
      }

      // 2. Telemetry HUD
      metricFps.textContent = (data.fps || 0).toFixed(1);
      metricLatency.textContent = (data.latency_ms || 0).toFixed(1);
      metricFaces.textContent = data.active_faces || 0;
      if (data.enrolled_count !== undefined) {
        metricEnrolled.textContent = data.enrolled_count;
      }

      // 2.1 Central Receiver Link Status from edge dispatcher
      if (data.central_receiver_online !== undefined) {
        if (data.central_receiver_online) {
          centralLinkStatus.innerHTML = '<span class="chip-dot"></span><span class="chip-text">CENTRAL RECEIVER: ONLINE</span>';
          centralLinkStatus.style.borderColor = 'rgba(0, 230, 118, 0.4)';
          centralLinkStatus.style.color = '#00e676';
        } else {
          centralLinkStatus.innerHTML = '<span class="chip-dot" style="background:#ffb300;box-shadow:0 0 6px #ffb300;"></span><span class="chip-text" style="color:#ffb300;">CENTRAL: OFFLINE (LOCAL QUEUE)</span>';
          centralLinkStatus.style.borderColor = 'rgba(255, 179, 0, 0.4)';
          centralLinkStatus.style.color = '#ffb300';
        }
      }

      // 3. Process recognitions with Deduplicated Student Log
      // "verify multiple times but show only once if the student appears"
      if (data.recognitions && data.recognitions.length > 0) {
        data.recognitions.forEach(rec => {
          if (rec.status === 'VERIFIED') {
            updateDeduplicatedStudent(rec);
          }
        });
      }

      // 4. Process Data Sent Receipts
      if (data.data_sent_receipts && data.data_sent_receipts.length > 0) {
        data.data_sent_receipts.forEach(rcpt => {
          const isQueued = rcpt.status === 'QUEUED_LOCAL';
          logDataSent(
            isQueued ? 'WARN' : 'SENT',
            `[${rcpt.status}] Sighting [${rcpt.event_id}] ➔ ${rcpt.target} | Student: ${rcpt.student_name} (${rcpt.student_id}) | Conf: ${rcpt.confidence}%`
          );
          triggerAlertBanner(rcpt.student_id, rcpt.student_name, rcpt.confidence, rcpt.status);
        });
      }

      // 5. Update Connected Nodes list
      if (data.connected_nodes) {
        renderConnectedNodes(data.connected_nodes);
      }
    }
  }

  // Deduplication Engine: Updates in place so each student appears only once
  function updateDeduplicatedStudent(rec) {
    const studentId = rec.student_id;
    const nowTimeStr = new Date().toLocaleTimeString();

    if (verifiedStudentsMap.has(studentId)) {
      // Student already in list: increment verifications and update last confirmed
      const entry = verifiedStudentsMap.get(studentId);
      entry.verifications += 1;
      entry.lastConfirmed = nowTimeStr;
      entry.confidence = Math.round(rec.confidence * 100);
      entry.status = rec.status;
      entry.student_name = rec.student_name;

      // Update existing DOM row without creating clutter
      const row = document.getElementById(`stu-row-${studentId}`);
      if (row) {
        row.querySelector('.col-verifs').innerHTML = `<strong>${entry.verifications}x</strong>`;
        row.querySelector('.col-last').textContent = entry.lastConfirmed;
        row.querySelector('.col-conf').textContent = `${entry.confidence}%`;
        row.classList.add('flash-row');
        setTimeout(() => row.classList.remove('flash-row'), 400);
      }
    } else {
      // First appearance: add single entry
      const newEntry = {
        student_id: studentId,
        student_name: rec.student_name,
        verifications: 1,
        firstSeen: nowTimeStr,
        lastConfirmed: nowTimeStr,
        confidence: Math.round(rec.confidence * 100),
        status: rec.status
      };
      verifiedStudentsMap.set(studentId, newEntry);
      renderDeduplicatedStudentRow(newEntry);
    }

    studentCountBadge.textContent = `${verifiedStudentsMap.size} Student(s)`;
  }

  function renderDeduplicatedStudentRow(s) {
    const empty = studentTableBody.querySelector('.empty-row');
    if (empty) empty.remove();

    const tr = document.createElement('tr');
    tr.id = `stu-row-${s.student_id}`;
    tr.className = 'student-row flash-row';
    tr.innerHTML = `
      <td><strong>${s.student_id}</strong></td>
      <td>${s.student_name}</td>
      <td class="col-verifs"><strong>${s.verifications}x</strong></td>
      <td><code>${s.firstSeen}</code></td>
      <td class="col-last"><code>${s.lastConfirmed}</code></td>
      <td><span class="tag-status tag-verified">${s.status}</span></td>
    `;
    studentTableBody.prepend(tr);
    setTimeout(() => tr.classList.remove('flash-row'), 400);
  }

  function triggerAlertBanner(stuId, stuName, conf, status = 'DISPATCHED') {
    bannerDesc.textContent = `Student [${stuId}] ${stuName} • ${conf}% Match → Central Audit Receiver`;
    const badge = eventBanner.querySelector('.banner-badge');
    if (badge) {
      if (status === 'QUEUED_LOCAL') {
        badge.textContent = 'LOCAL QUEUE';
        badge.style.background = 'rgba(255, 179, 0, 0.25)';
        badge.style.borderColor = '#ffb300';
      } else {
        badge.textContent = 'DISPATCHED';
        badge.style.background = 'rgba(0, 229, 255, 0.25)';
        badge.style.borderColor = '#00e5ff';
      }
    }
    eventBanner.classList.add('show');

    if (bannerTimer) clearTimeout(bannerTimer);
    bannerTimer = setTimeout(() => {
      eventBanner.classList.remove('show');
    }, 2800);
  }

  // ---------------------------------------------------------------------------
  // 5. Data Sent Logs (Transmission Terminal)
  // ---------------------------------------------------------------------------
  function logDataSent(level, text) {
    const line = document.createElement('div');
    const ts = new Date().toLocaleTimeString();
    line.className = `term-line ${level.toLowerCase()}`;
    line.innerHTML = `<span class="term-time">[${ts}]</span> <span class="term-tag tag-${level.toLowerCase()}">${level}</span> ${escapeHtml(text)}`;

    dataSentTerminal.appendChild(line);
    dataSentTerminal.scrollTop = dataSentTerminal.scrollHeight;

    while (dataSentTerminal.children.length > 50) {
      dataSentTerminal.removeChild(dataSentTerminal.firstChild);
    }
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ---------------------------------------------------------------------------
  // 6. Connected Nodes List Rendering
  // ---------------------------------------------------------------------------
  function renderConnectedNodes(nodesList) {
    const currentCamId = inputCameraId.value.trim() || 'CAM-01';
    let onlineCount = 0;

    let html = '';
    nodesList.forEach(n => {
      const isCurrent = n.camera_id === currentCamId;
      const isOnline = n.online !== false;
      if (isOnline) onlineCount++;

      html += `
        <div class="connected-item ${isCurrent ? 'current' : ''}">
          <div class="node-dot ${isOnline ? 'online' : 'offline'}"></div>
          <div class="node-info">
            <span class="node-name">${n.camera_id} ${isCurrent ? '(This Node)' : ''}</span>
            <span class="node-sub">${n.location || 'Entrance'}</span>
          </div>
          <span class="node-badge ${isCurrent ? 'badge-local' : (isOnline ? 'badge-peer' : 'badge-off')}">
            ${isCurrent ? 'LOCAL' : (isOnline ? 'PEER' : 'OFFLINE')}
          </span>
        </div>
      `;
    });

    connectedNodesList.innerHTML = html;
    connectedCountBadge.textContent = `${onlineCount} Online`;
  }

  // ---------------------------------------------------------------------------
  // 7. Face Enrollment Modal & Actions
  // ---------------------------------------------------------------------------
  async function openEnrollmentModal() {
    if (!isRunning) {
      await startCameraNode();
      // Brief pause to allow the camera sensor to produce a warm frame
      await new Promise(r => setTimeout(r, 600));
    }

    enrollCanvas.width = captureCanvas.width || 640;
    enrollCanvas.height = captureCanvas.height || 480;
    const eCtx = enrollCanvas.getContext('2d');
    if (captureCanvas.width > 0) {
      eCtx.drawImage(captureCanvas, 0, 0);
    } else if (video && video.videoWidth > 0) {
      eCtx.drawImage(video, 0, 0, enrollCanvas.width, enrollCanvas.height);
    }

    enrollError.style.display = 'none';
    enrollModal.classList.add('active');
    enrollStudentId.focus();
  }

  function closeEnrollmentModal() {
    enrollModal.classList.remove('active');
  }

  async function submitEnrollment() {
    const stuId = enrollStudentId.value.trim();
    const name = enrollName.value.trim();
    const dept = enrollDept.value.trim() || 'Engineering';
    const batch = enrollBatch.value.trim() || '2026';

    if (!stuId || !name) {
      enrollError.textContent = 'Student ID and Full Name are required.';
      enrollError.style.display = 'block';
      return;
    }

    const b64 = enrollCanvas.toDataURL('image/jpeg', 0.95);
    btnSubmitEnroll.disabled = true;
    btnSubmitEnroll.textContent = 'Enrolling Template...';
    enrollError.style.display = 'none';

    try {
      const res = await fetch('/api/enroll-face', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          student_id: stuId,
          name: name,
          image_base64: b64,
          department: dept,
          batch: batch
        })
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Enrollment failed');

      metricEnrolled.textContent = data.enrolled_count;
      closeEnrollmentModal();
      logDataSent('SUCCESS', `Student enrolled: ${name} [${stuId}]. Total enrolled: ${data.enrolled_count}`);
      alert(`Success: ${data.message}`);
    } catch (err) {
      enrollError.textContent = err.message || 'Enrollment error';
      enrollError.style.display = 'block';
    } finally {
      btnSubmitEnroll.disabled = false;
      btnSubmitEnroll.textContent = 'Save & Enroll Template';
    }
  }

  function takeSnapshot() {
    if (!isRunning) {
      alert('Start the camera node first to capture snapshots.');
      return;
    }
    const snapCanvas = canvas.width > 0 ? canvas : captureCanvas;
    const a = document.createElement('a');
    a.href = snapCanvas.toDataURL('image/jpeg', 0.95);
    a.download = `cameranode-${inputCameraId.value.trim()}-${Date.now()}.jpg`;
    a.click();
    logDataSent('INFO', 'Saved camera snapshot image.');
  }

  // ---------------------------------------------------------------------------
  // 8. Event Listeners & Startup
  // ---------------------------------------------------------------------------
  function setupEventListeners() {
    btnToggleCamera.addEventListener('click', () => {
      if (isRunning) stopCameraNode();
      else startCameraNode();
    });

    btnStartOverlay.addEventListener('click', startCameraNode);

    cameraSelect.addEventListener('change', () => {
      if (isRunning) startCameraNode();
    });

    btnApplyConfig.addEventListener('click', () => {
      sendNodeConfig();
      btnApplyConfig.textContent = '✓ Config Applied';
      setTimeout(() => {
        btnApplyConfig.textContent = 'Apply Node Identity';
      }, 1500);
      logDataSent('INFO', `Node identity updated: ${inputCameraId.value} @ ${inputLocation.value}`);
    });

    btnSnapshot.addEventListener('click', takeSnapshot);
    btnEnrollModal.addEventListener('click', openEnrollmentModal);
    btnCloseModal.addEventListener('click', closeEnrollmentModal);
    btnCancelEnroll.addEventListener('click', closeEnrollmentModal);
    btnSubmitEnroll.addEventListener('click', submitEnrollment);

    // Sidebar Mobile Toggle
    const btnSidebarToggle = document.getElementById('btnSidebarToggle');
    const dashSidebar = document.getElementById('dashSidebar');
    if (btnSidebarToggle && dashSidebar) {
      btnSidebarToggle.addEventListener('click', () => {
        dashSidebar.classList.toggle('sidebar-open');
      });
      // Close sidebar when clicking outside on mobile
      document.addEventListener('click', (e) => {
        if (dashSidebar.classList.contains('sidebar-open') &&
            !dashSidebar.contains(e.target) &&
            e.target !== btnSidebarToggle) {
          dashSidebar.classList.remove('sidebar-open');
        }
      });
    }

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && enrollModal.classList.contains('active')) {
        closeEnrollmentModal();
      }
    });
  }

  async function init() {
    checkSecureContext();
    setupEventListeners();
    await enumerateCameras();
    try {
      const res = await fetch('/api/node-info');
      if (res.ok) {
        const info = await res.json();
        metricEnrolled.textContent = info.enrolled_count || 1;
      }
    } catch (e) {
      console.warn('Could not fetch node info:', e);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
