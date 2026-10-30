/**
 * NetCam Centralized Monitoring Dashboard Controller
 * Real-time Multi-Camera Feeds, Dynamic Topology Map with Confidence Green Shading & Directional Edges
 */

(function () {
  'use strict';

  // DOM Elements - Header & Stats
  const statActiveNodes = document.getElementById('statActiveNodes');
  const statUniqueVerified = document.getElementById('statUniqueVerified');
  const statTotalSightings = document.getElementById('statTotalSightings');

  // DOM Elements - Topology Map
  const topoMapSvg = document.getElementById('topoMapSvg');
  const topoEdgesLayer = document.getElementById('topoEdgesLayer');
  const topoNodesLayer = document.getElementById('topoNodesLayer');
  const topoEmptyBanner = document.getElementById('topoEmptyBanner');
  const topoStatusText = document.getElementById('topoStatusText');
  const topoTransitionTicker = document.getElementById('topoTransitionTicker');
  const topoLiveDot = document.getElementById('topoLiveDot');

  // DOM Elements - Camera Grid
  const dashNodeCount = document.getElementById('dashNodeCount');
  const dashCameraMatrix = document.getElementById('dashCameraMatrix');

  // DOM Elements - Tables & Filters
  const tabUniqueStudents = document.getElementById('tabUniqueStudents');
  const tabAllEvents = document.getElementById('tabAllEvents');
  const tableUniqueStudents = document.getElementById('tableUniqueStudents');
  const dashUniqueTableBody = document.getElementById('dashUniqueTableBody');
  const tableAllEvents = document.getElementById('tableAllEvents');
  const dashAllEventsTableBody = document.getElementById('dashAllEventsTableBody');
  const filterStudentId = document.getElementById('filterStudentId');
  const btnRefreshDb = document.getElementById('btnRefreshDb');

  // State
  let ws = null;
  let activeNodesMap = {};       // camera_id -> node_data
  let nodeCoordinates = {};      // camera_id -> { x, y }
  let nodeDetectionState = {};   // camera_id -> { confidence, studentName, studentId, lastSeenTime }
  let studentLastCamera = {};    // student_id -> last_camera_id
  let activeTransitions = [];    // list of active cross-camera edges
  let currentTab = 'unique';     // 'unique' or 'all'

  // ---------------------------------------------------------------------------
  // 1. Initialization
  // ---------------------------------------------------------------------------
  async function init() {
    setupTabSwitching();
    setupSidebarAndNav();
    setupModal();
    await fetchStats();
    await fetchConnectedNodes();
    await fetchTrajectories();
    await fetchEvents();
    await fetchEnrolledStudents();
    connectDashboardWebSocket();

    // Auto-refresh interval (every 4s)
    setInterval(() => {
      fetchStats();
      fetchConnectedNodes();
      fetchEvents();
      fetchEnrolledStudents();
      decayNodeDetections();
    }, 4000);

    // High frequency tick for smooth edge/node decay (every 1s)
    setInterval(decayNodeDetections, 1000);

    btnRefreshDb.addEventListener('click', () => {
      fetchStats();
      fetchConnectedNodes();
      fetchEvents();
    });

    filterStudentId.addEventListener('input', () => {
      fetchEvents();
    });
  }

  function setupTabSwitching() {
    tabUniqueStudents.addEventListener('click', () => {
      currentTab = 'unique';
      tabUniqueStudents.classList.add('active');
      tabAllEvents.classList.remove('active');
      tableUniqueStudents.style.display = '';
      tableAllEvents.style.display = 'none';
      fetchEvents();
    });

    tabAllEvents.addEventListener('click', () => {
      currentTab = 'all';
      tabAllEvents.classList.add('active');
      tabUniqueStudents.classList.remove('active');
      tableUniqueStudents.style.display = 'none';
      tableAllEvents.style.display = '';
      fetchEvents();
    });
  }

  // ---------------------------------------------------------------------------
  // 2. WebSocket Real-Time Connection
  // ---------------------------------------------------------------------------
  function connectDashboardWebSocket() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${window.location.host}/ws/dashboard`;

    ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'SNAPSHOT') {
          handleSnapshot(msg);
        } else if (msg.type === 'NODE_UPDATED') {
          activeNodesMap[msg.data.camera_id] = msg.data;
          renderTopologyMap();
          renderCameraMatrix();
        } else if (msg.type === 'NODE_FRAME') {
          handleLiveFrame(msg.data);
        } else if (msg.type === 'NEW_SIGHTING') {
          handleNewSighting(msg.data);
          fetchStats();
          fetchEvents();
        } else if (msg.type === 'EVENT_ACK') {
          fetchStats();
          fetchEvents();
        } else if (msg.type === 'NODE_OFFLINE') {
          if (activeNodesMap[msg.data.camera_id]) {
            activeNodesMap[msg.data.camera_id].online = false;
            activeNodesMap[msg.data.camera_id].fps = 0;
            renderTopologyMap();
            renderCameraMatrix();
          }
        }
      } catch (e) {
        console.error('Error in dashboard WS:', e);
      }
    };

    ws.onclose = () => {
      setTimeout(connectDashboardWebSocket, 2000);
    };
  }

  function handleSnapshot(data) {
    if (data.nodes) {
      data.nodes.forEach(n => { activeNodesMap[n.camera_id] = n; });
      renderTopologyMap();
      renderCameraMatrix();
    }
    if (data.unique_verified !== undefined) {
      statUniqueVerified.textContent = data.unique_verified;
    }
    if (data.total_sightings !== undefined) {
      statTotalSightings.textContent = data.total_sightings;
    }
    if (data.trajectories) {
      data.trajectories.forEach(t => {
        if (t.student_id && t.current_camera) {
          studentLastCamera[t.student_id] = t.current_camera;
        }
      });
    }
  }

  function handleLiveFrame(data) {
    // 1. Update Map Data
    const camId = data.camera_id;
    const isNew = !activeNodesMap[camId];
    data.lastSeenLive = Date.now();
    activeNodesMap[camId] = Object.assign(activeNodesMap[camId] || {}, data, { online: true });

    if (isNew) {
      renderTopologyMap();
      renderCameraMatrix();
      return;
    }

    // 2. If face detection is active on this frame, update node state STRICTLY for VERIFIED students
    if (data.active_students && data.active_students.length > 0) {
      data.active_students.forEach(st => {
        if (st.status === 'VERIFIED' && st.student_id && st.student_id !== 'UNKNOWN') {
          updateNodeStudent(camId, {
            confidence: st.confidence,
            studentName: st.student_name || 'Authorized Student',
            studentId: st.student_id,
            status: 'VERIFIED'
          });
        }
      });
    } else if (data.active_faces > 0 && data.latest_confidence > 0) {
      if (data.latest_status === 'VERIFIED' && data.latest_student && data.latest_student !== 'UNKNOWN') {
        updateNodeStudent(camId, {
          confidence: data.latest_confidence,
          studentName: data.latest_name || 'Authorized Student',
          studentId: data.latest_student,
          status: 'VERIFIED'
        });
      }
    }

    // 3. Update Camera Card and dynamically prioritize live streaming cameras at the top
    const card = document.getElementById(`cam-card-${camId}`);
    if (card) {
      card.classList.remove('offline');
      card.classList.add('online', 'is-streaming');

      const img = card.querySelector('.dash-cam-img');
      const placeholder = card.querySelector('.thumb-placeholder');
      if (data.latest_thumbnail) {
        if (img) {
          img.src = data.latest_thumbnail;
          img.style.display = 'block';
        }
        if (placeholder) placeholder.style.display = 'none';
      }
      const fpsBadge = card.querySelector('.badge-fps');
      if (fpsBadge) {
        fpsBadge.className = 'pill-badge badge-fps pill-green';
        fpsBadge.textContent = `● LIVE · ${Number(data.fps || 0).toFixed(1)} FPS`;
      }

      // If a non-streaming card is positioned ahead of this live streaming card, hoist it above
      if (dashCameraMatrix && card.previousElementSibling && !card.previousElementSibling.classList.contains('is-streaming')) {
        const firstNonStreaming = dashCameraMatrix.querySelector('.dash-cam-card:not(.is-streaming)');
        if (firstNonStreaming && firstNonStreaming !== card) {
          dashCameraMatrix.insertBefore(card, firstNonStreaming);
        }
      }
    } else {
      renderCameraMatrix();
    }
  }

  function handleNewSighting(data) {
    const sighting = data.sighting || {};
    const camId = sighting.camera_id;
    const stuId = sighting.student_id;
    const stuName = sighting.student_name || 'Student';
    const status = sighting.status;
    const conf = (sighting.confidence || 75) / 100.0;
    const now = Date.now();

    // Strict Rule: Trajectories & dynamic map detections are ONLY for VERIFIED users
    if (status !== 'VERIFIED' || !stuId || stuId === 'UNKNOWN') {
      return;
    }

    // Trigger detection color change on node for verified users
    if (camId) {
      updateNodeStudent(camId, {
        confidence: conf,
        studentName: stuName,
        studentId: stuId,
        status: 'VERIFIED'
      });
    }

    // Check cross-camera presence: Sequential Transition vs Concurrent Dual Presence
    const prevRecord = studentLastCamera[stuId];
    if (prevRecord) {
      const prevCam = typeof prevRecord === 'string' ? prevRecord : prevRecord.cameraId;
      const prevTime = typeof prevRecord === 'object' && prevRecord.timestamp ? prevRecord.timestamp : 0;

      if (prevCam && prevCam !== camId && activeNodesMap[prevCam]) {
        const timeDelta = now - prevTime;
        // Check if student is still actively illuminated in previous camera (under 6.5s decay)
        const isStillActiveInPrevCam = Boolean(
          nodeDetectionState[prevCam] &&
          nodeDetectionState[prevCam][stuId] &&
          (now - (nodeDetectionState[prevCam][stuId].lastSeenTime || 0) < 6500)
        );

        // If sighting occurs within 3.5s or student is actively seen in both cameras -> Overlap Zone
        const isOverlap = (prevTime > 0 && timeDelta <= 3500) || isStillActiveInPrevCam;
        addTransitionEdge(prevCam, camId, stuId, stuName, isOverlap ? 'OVERLAP' : 'TRANSITION');
      }
    }

    studentLastCamera[stuId] = {
      cameraId: camId,
      timestamp: now
    };
  }

  // ---------------------------------------------------------------------------
  // 3. Dynamic Topological Map Engine
  // ---------------------------------------------------------------------------
  function getConfidenceShade(conf) {
    // Gray (Default) -> Light Green -> Medium Green -> Ultra Electric Green
    if (conf === undefined || conf === null || conf <= 0) {
      return {
        fill: '#1e2632',
        stroke: '#475569',
        glow: 'none',
        textColor: '#94a3b8',
        filter: 'none',
        label: 'Standby',
        level: 0
      };
    } else if (conf < 0.60) {
      return {
        fill: '#14532d',            // Low Green (Dark forest)
        stroke: '#4ade80',          // Soft green stroke
        glow: '0 0 10px rgba(74, 222, 128, 0.5)',
        textColor: '#4ade80',
        filter: 'none',
        label: `${Math.round(conf * 100)}% (Low)`,
        level: 1
      };
    } else if (conf < 0.80) {
      return {
        fill: '#047857',            // Medium Green (Vibrant jade)
        stroke: '#10b981',          // Bright emerald stroke
        glow: '0 0 16px rgba(16, 185, 129, 0.75)',
        textColor: '#10b981',
        filter: 'none',
        label: `${Math.round(conf * 100)}% (Med)`,
        level: 2
      };
    } else {
      return {
        fill: '#00ff88',            // Ultra Vibrant Electric Neon Green!
        stroke: '#ffffff',          // White hot border
        glow: '0 0 24px #00ff88',
        textColor: '#00ff88',
        filter: 'url(#glowPeakGreen)',
        label: `${Math.round(conf * 100)}% (High)`,
        level: 3
      };
    }
  }

  function updateNodeStudent(camId, info) {
    // Strict Guard: ONLY verified users trigger node detections on the map
    if (!info.studentId || info.studentId === 'UNKNOWN' || info.status !== 'VERIFIED') {
      return;
    }

    if (!nodeDetectionState[camId]) {
      nodeDetectionState[camId] = {};
    }

    const key = info.studentId;
    nodeDetectionState[camId][key] = {
      studentId: info.studentId,
      studentName: info.studentName,
      confidence: info.confidence,
      status: 'VERIFIED',
      lastSeenTime: Date.now()
    };

    renderNodeBadges(camId);
  }

  function renderNodeBadges(camId) {
    const nodeEl = document.getElementById(`topo-node-wrap-${camId}`);
    if (!nodeEl) return;

    const studentsMap = nodeDetectionState[camId] || {};
    // Strict Filter: Only verified users in the capsule box
    const students = Object.values(studentsMap).filter(st => st.status === 'VERIFIED' && st.studentId && st.studentId !== 'UNKNOWN');

    const circleEl = nodeEl.querySelector('.node-center-circle');
    const pulseEl = nodeEl.querySelector('.node-pulse-ring');
    const badgesLayer = nodeEl.querySelector('.node-badges-layer');

    if (students.length === 0) {
      const shade = getConfidenceShade(0);
      if (circleEl) {
        circleEl.setAttribute('fill', shade.fill);
        circleEl.setAttribute('stroke', shade.stroke);
        circleEl.removeAttribute('filter');
      }
      if (pulseEl) pulseEl.style.display = 'none';
      if (badgesLayer) badgesLayer.innerHTML = '';
      return;
    }

    // Sort students by confidence descending
    students.sort((a, b) => b.confidence - a.confidence);

    const maxConf = students[0].confidence;
    const shade = getConfidenceShade(maxConf);

    if (circleEl) {
      circleEl.setAttribute('fill', shade.fill);
      circleEl.setAttribute('stroke', shade.stroke);
      if (shade.filter !== 'none') {
        circleEl.setAttribute('filter', shade.filter);
      } else {
        circleEl.removeAttribute('filter');
      }
    }

    if (pulseEl) {
      pulseEl.setAttribute('stroke', shade.stroke);
      pulseEl.style.display = 'block';
    }

    // Render unified expanding capsule box
    const isMultiPerson = students.length > 1;
    const visibleStudents = students.slice(0, 4);
    const overflowCount = students.length - visibleStudents.length;

    const rowHeight = 22;
    const headerHeight = isMultiPerson ? 20 : 0;
    const padY = 8;
    const totalH = padY * 2 + headerHeight + (visibleStudents.length * rowHeight) + (overflowCount > 0 ? 18 : 0);

    // Calculate dynamic capsule width based on longest student name or header
    const maxLen = Math.max(isMultiPerson ? 20 : 12, ...visibleStudents.map(s => (s.studentName || s.studentId).length));
    const capsuleW = Math.min(270, Math.max(180, maxLen * 8 + 60));
    const capsuleX = -capsuleW / 2;
    const capsuleY = -38 - totalH; // sits neatly above the camera circle

    let capsuleHeaderHtml = '';
    if (isMultiPerson) {
      const headY = capsuleY + padY + 11;
      const headDivY = capsuleY + padY + 17;
      capsuleHeaderHtml = `
        <text x="0" y="${headY}" text-anchor="middle" font-size="9.5" font-family="'JetBrains Mono', monospace" font-weight="700" fill="#00e5ff" letter-spacing="0.5">
          👥 ${students.length} VERIFIED PRESENT
        </text>
        <line x1="${capsuleX + 12}" y1="${headDivY}" x2="${capsuleX + capsuleW - 12}" y2="${headDivY}" stroke="rgba(0, 229, 255, 0.25)" stroke-width="1" />
      `;
    }

    let capsuleRowsHtml = '';
    visibleStudents.forEach((st, idx) => {
      const rowY = capsuleY + padY + headerHeight + (idx * rowHeight) + 15;
      const stShade = getConfidenceShade(st.confidence);
      const confPct = Math.round(st.confidence * 100);

      // Divider line between student rows
      let dividerHtml = '';
      if (idx > 0) {
        const divY = capsuleY + padY + headerHeight + (idx * rowHeight);
        dividerHtml = `<line x1="${capsuleX + 12}" y1="${divY}" x2="${capsuleX + capsuleW - 12}" y2="${divY}" stroke="rgba(255, 255, 255, 0.09)" stroke-width="1" />`;
      }

      capsuleRowsHtml += `
        ${dividerHtml}
        <g class="capsule-student-row">
          <text x="0" y="${rowY}" text-anchor="middle" font-size="10.5" font-family="'JetBrains Mono', monospace" font-weight="700" fill="${stShade.textColor}">
            👤 ${st.studentName} [${confPct}%]
          </text>
        </g>
      `;
    });

    if (overflowCount > 0) {
      const overY = capsuleY + padY + headerHeight + (visibleStudents.length * rowHeight) + 13;
      const divY = capsuleY + padY + headerHeight + (visibleStudents.length * rowHeight);
      capsuleRowsHtml += `
        <line x1="${capsuleX + 12}" y1="${divY}" x2="${capsuleX + capsuleW - 12}" y2="${divY}" stroke="rgba(0, 229, 255, 0.2)" stroke-width="1" />
        <text x="0" y="${overY}" text-anchor="middle" font-size="9" font-family="'Inter', sans-serif" font-weight="700" fill="#00e5ff">
          +${overflowCount} more student${overflowCount > 1 ? 's' : ''} detected
        </text>
      `;
    }

    const capsuleBorderColor = isMultiPerson ? '#00e5ff' : shade.stroke;

    const capsuleHtml = `
      <g class="node-capsule-wrap">
        <!-- Unified Expanding Capsule Box -->
        <rect class="node-capsule-box" 
              x="${capsuleX}" y="${capsuleY}" 
              width="${capsuleW}" height="${totalH}" 
              rx="13" ry="13"
              fill="rgba(15, 23, 42, 0.95)" 
              stroke="${capsuleBorderColor}" 
              stroke-width="${isMultiPerson ? 2 : 1.5}" 
              filter="drop-shadow(0 4px 14px rgba(0,0,0,0.7))" />

        <!-- Anchor Pointer Stem pointing to camera node -->
        <polygon points="-6,${-38} 0,${-33} 6,${-38}" 
                 fill="rgba(15, 23, 42, 0.95)" 
                 stroke="${capsuleBorderColor}" 
                 stroke-width="${isMultiPerson ? 2 : 1.5}" />

        <!-- Multi-Student Header if multiple present -->
        ${capsuleHeaderHtml}

        <!-- Student Rows Appended Inside Capsule -->
        ${capsuleRowsHtml}
      </g>
    `;

    if (badgesLayer) {
      badgesLayer.innerHTML = capsuleHtml;
    }

    // Update bottom ticker with all student names
    const namesList = students.map(s => `<strong>${s.studentName}</strong> [${Math.round(s.confidence * 100)}%]`).join(', ');
    if (isMultiPerson) {
      topoTransitionTicker.innerHTML = `
        <span class="ticker-highlight" style="color:#00e5ff;">👥 Group at ${camId} (${students.length} students):</span> ${namesList}
      `;
    } else {
      topoTransitionTicker.innerHTML = `
        <span class="ticker-highlight">● Active at ${camId}:</span> ${namesList}
      `;
    }
  }

  function decayNodeDetections() {
    const now = Date.now();

    for (const camId in nodeDetectionState) {
      const studentsMap = nodeDetectionState[camId];
      if (studentsMap) {
        let countBefore = Object.keys(studentsMap).length;
        for (const stuKey in studentsMap) {
          if (now - studentsMap[stuKey].lastSeenTime > 6500) {
            delete studentsMap[stuKey];
          }
        }
        if (countBefore !== Object.keys(studentsMap).length) {
          renderNodeBadges(camId);
        }
      }
    }

    // Clean up expired transition edges (older than 10s)
    if (activeTransitions.length > 0) {
      const prevLen = activeTransitions.length;
      activeTransitions = activeTransitions.filter(t => (now - t.timestamp < 10000));
      if (activeTransitions.length !== prevLen) {
        renderEdges();
      }
    }
  }

  const STUDENT_PALETTE = [
    { stroke: '#00ff88', bg: '#061f14', marker: 'url(#edgeArrowGreen)', tagText: '#00ff88' },
    { stroke: '#00e5ff', bg: '#031b26', marker: 'url(#edgeArrowCyan)', tagText: '#00e5ff' },
    { stroke: '#ffb300', bg: '#2b1a03', marker: 'url(#edgeArrowAmber)', tagText: '#ffb300' },
    { stroke: '#d946ef', bg: '#260a2b', marker: 'url(#edgeArrowPurple)', tagText: '#d946ef' }
  ];

  function getStudentColor(studentId) {
    if (!studentId) return STUDENT_PALETTE[0];
    let hash = 0;
    const str = String(studentId);
    for (let i = 0; i < str.length; i++) {
      hash = (hash << 5) - hash + str.charCodeAt(i);
      hash |= 0;
    }
    const idx = Math.abs(hash) % STUDENT_PALETTE.length;
    return STUDENT_PALETTE[idx];
  }

  function addTransitionEdge(fromCam, toCam, studentId, studentName, edgeType = 'TRANSITION') {
    if (!fromCam || !toCam || fromCam === toCam) return;
    if (!studentId || studentId === 'UNKNOWN') return;

    // Deduplicate: If an edge already exists between these two nodes (in either direction if OVERLAP)
    const existing = activeTransitions.find(t => 
      (t.from === fromCam && t.to === toCam && t.studentId === studentId) ||
      (edgeType === 'OVERLAP' && t.from === toCam && t.to === fromCam && t.studentId === studentId)
    );

    if (existing) {
      existing.studentId = studentId;
      existing.studentName = studentName;
      existing.type = edgeType;
      existing.timestamp = Date.now();
    } else {
      activeTransitions.push({
        from: fromCam,
        to: toCam,
        studentId: studentId,
        studentName: studentName,
        type: edgeType,
        timestamp: Date.now()
      });
    }

    renderEdges();

    // Update status ticker based on edgeType
    if (edgeType === 'OVERLAP') {
      topoTransitionTicker.innerHTML = `
        <span class="ticker-overlap-pill">⚡ DUAL PRESENCE:</span>
        <strong>${studentName}</strong> sighted concurrently on <code class="code-id">${fromCam}</code> &amp; <code class="code-id">${toCam}</code> (Overlap Zone)
      `;
    } else {
      topoTransitionTicker.innerHTML = `
        <span class="ticker-transition-pill">➔ TRAJECTORY:</span>
        <strong>${studentName}</strong> transit: <code class="code-id">${fromCam}</code> ➔ <code class="code-id">${toCam}</code>
      `;
    }
  }

  function calculateNodePositions(nodes) {
    const count = nodes.length;
    nodeCoordinates = {};

    if (count === 0) return;

    if (count === 1) {
      nodeCoordinates[nodes[0].camera_id] = { x: 500, y: 280 };
      return;
    }

    if (count === 2) {
      nodeCoordinates[nodes[0].camera_id] = { x: 260, y: 280 };
      nodeCoordinates[nodes[1].camera_id] = { x: 740, y: 280 };
      return;
    }

    if (count === 3) {
      nodeCoordinates[nodes[0].camera_id] = { x: 200, y: 280 };
      nodeCoordinates[nodes[1].camera_id] = { x: 500, y: 230 };
      nodeCoordinates[nodes[2].camera_id] = { x: 800, y: 280 };
      return;
    }

    // N nodes: evenly spaced across horizontal canvas
    const padX = 140;
    const step = (1000 - 2 * padX) / Math.max(1, count - 1);
    nodes.forEach((n, idx) => {
      const x = padX + idx * step;
      const y = 280 + (idx % 2 === 0 ? -45 : 45);
      nodeCoordinates[n.camera_id] = { x, y };
    });
  }

  function renderTopologyMap() {
    const nodes = Object.values(activeNodesMap).filter(n => n.online !== false);

    if (nodes.length === 0) {
      topoEmptyBanner.style.display = 'flex';
      topoNodesLayer.innerHTML = '';
      topoEdgesLayer.innerHTML = '';
      topoStatusText.textContent = 'Network Scanner Ready • 0 Nodes Plotted';
      return;
    }

    topoEmptyBanner.style.display = 'none';
    topoStatusText.textContent = `Active Grid • ${nodes.length} Camera Node${nodes.length === 1 ? '' : 's'} Online`;

    // Sort nodes deterministically
    nodes.sort((a, b) => a.camera_id.localeCompare(b.camera_id));
    calculateNodePositions(nodes);

    // 1. Render Nodes with high-contrast, beautiful UI
    let nodesHtml = '';
    nodes.forEach(node => {
      const pos = nodeCoordinates[node.camera_id] || { x: 500, y: 170 };

      nodesHtml += `
        <g id="topo-node-wrap-${node.camera_id}" class="topo-node-group" transform="translate(${pos.x}, ${pos.y})">
          <!-- Animated Radar Wave (When Face is Detected) -->
          <circle class="node-pulse-ring" r="38" stroke="#00ff88" stroke-width="2" fill="none" style="display:none;"></circle>
          
          <!-- Outer Circle Ring -->
          <circle class="node-outer-ring" r="32" stroke="rgba(255, 255, 255, 0.22)" stroke-width="1.5" fill="none"></circle>
          
          <!-- Core Center Circle -->
          <circle class="node-center-circle" r="26" fill="#131b2e" stroke="#334155" stroke-width="2.5"></circle>
          
          <!-- Camera Glyphs -->
          <text class="node-icon" y="6" text-anchor="middle" font-size="14" fill="#ffffff">📹</text>
          
          <!-- Dedicated High-Contrast Node Info Card -->
          <g class="node-plate" transform="translate(0, 36)">
            <!-- Card Backdrop -->
            <rect class="node-card-bg" x="-75" y="0" width="150" height="42" rx="8" ry="8"></rect>
            
            <!-- Online Indicator Dot -->
            <circle class="node-status-dot" cx="-56" cy="15" r="3.5"></circle>
            
            <!-- Node ID Label (Bold Crisp White) -->
            <text class="node-label-id" x="-46" y="19" text-anchor="start">${node.camera_id}</text>
            
            <!-- Online Status Text -->
            <text class="node-status-text" x="58" y="19" text-anchor="end">ONLINE</text>
            
            <!-- Node Location Sub-label (Crisp Slate Gray) -->
            <text class="node-label-loc" x="0" y="33" text-anchor="middle">${node.location || 'Camera Station'}</text>
          </g>

          <!-- Dynamic Multi-Student Badges Layer (Stacked Non-Conflicting Pills) -->
          <g class="node-badges-layer"></g>
        </g>
      `;
    });

    topoNodesLayer.innerHTML = nodesHtml;

    // Immediately update badges and colors for each node
    nodes.forEach(node => {
      renderNodeBadges(node.camera_id);
    });

    // 2. Render Existing Edges
    renderEdges();
  }

  function renderEdges() {
    let edgesHtml = '';

    activeTransitions.forEach(trans => {
      const p1 = nodeCoordinates[trans.from];
      const p2 = nodeCoordinates[trans.to];

      if (!p1 || !p2) return;

      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dist < 30) return;

      const isOverlap = trans.type === 'OVERLAP';
      const studentColor = getStudentColor(trans.studentId);

      // Determine arch curvature based on horizontal direction
      // Left-to-right arches upward (negative Y offset)
      // Right-to-left arches downward (positive Y offset)
      // This completely prevents forward/backward arrows from overlapping!
      const archSign = dx >= 0 ? -1 : 1;
      const archHeight = Math.min(65, Math.max(35, dist * 0.18));
      
      const midX = (p1.x + p2.x) / 2;
      const midY = (p1.y + p2.y) / 2 + (isOverlap ? 0 : archSign * archHeight);

      // Trim start point (p1 -> mid)
      const dx1 = midX - p1.x;
      const dy1 = midY - p1.y;
      const d1 = Math.sqrt(dx1 * dx1 + dy1 * dy1) || 1;
      const rStart = 38;
      const x1 = p1.x + (dx1 * rStart) / d1;
      const y1 = p1.y + (dy1 * rStart) / d1;

      // Trim end point (mid -> p2)
      const dx2 = p2.x - midX;
      const dy2 = p2.y - midY;
      const d2 = Math.sqrt(dx2 * dx2 + dy2 * dy2) || 1;
      const rEnd = isOverlap ? 38 : 44; // if overlap, no arrowhead needed
      const x2 = p2.x - (dx2 * rEnd) / d2;
      const y2 = p2.y - (dy2 * rEnd) / d2;

      const studentDisplay = trans.studentName || trans.studentId || 'Verified Sighting';

      if (isOverlap) {
        // SIMULTANEOUS / OVERLAP: Dashed non-directional resonance beam
        edgesHtml += `
          <g class="edge-group edge-overlap-group">
            <path d="M ${x1} ${y1} L ${x2} ${y2}" 
                  class="topo-edge-overlap"></path>

            <!-- Floating Dual-Presence Tag on Overlap Beam -->
            <g class="edge-tag edge-tag-overlap" transform="translate(${midX}, ${midY})">
              <rect x="-82" y="-12" width="164" height="24" rx="12" 
                    fill="#031b26" stroke="#00e5ff" stroke-width="1.5"></rect>
              <text text-anchor="middle" y="4" font-size="9.5" font-family="'JetBrains Mono', monospace" font-weight="700" fill="#00e5ff">
                ⚡ DUAL: ${studentDisplay}
              </text>
            </g>
          </g>
        `;
      } else {
        // SEQUENTIAL TRANSITION: Directional colored arrow
        edgesHtml += `
          <g class="edge-group">
            <!-- Flowing Glowing Edge Path -->
            <path d="M ${x1} ${y1} Q ${midX} ${midY} ${x2} ${y2}" 
                  class="topo-edge-path" 
                  stroke="${studentColor.stroke}"
                  marker-end="${studentColor.marker}"></path>

            <!-- Floating Movement Tag on Edge -->
            <g class="edge-tag" transform="translate(${midX}, ${midY})">
              <rect x="-68" y="-12" width="136" height="24" rx="12" 
                    fill="${studentColor.bg}" stroke="${studentColor.stroke}" stroke-width="1.5"></rect>
              <text text-anchor="middle" y="4" font-size="10" font-family="'JetBrains Mono', monospace" font-weight="700" fill="${studentColor.tagText}">
                ➔ ${studentDisplay}
              </text>
            </g>
          </g>
        `;
      }
    });

    topoEdgesLayer.innerHTML = edgesHtml;
  }

  async function fetchTrajectories() {
    try {
      const res = await fetch('/api/trajectories');
      if (!res.ok) return;
      const trajs = await res.json();
      trajs.forEach(t => {
        // Enforce verified student check
        if (t.student_id && t.student_id !== 'UNKNOWN' && t.current_camera) {
          studentLastCamera[t.student_id] = t.current_camera;
        }
      });
    } catch (e) {
      console.warn('Could not fetch trajectories:', e);
    }
  }

  // ---------------------------------------------------------------------------
  // 4. Multi-Camera Grid Rendering
  // ---------------------------------------------------------------------------
  async function fetchConnectedNodes() {
    try {
      const res = await fetch('/api/connected-nodes');
      if (!res.ok) return;
      const nodes = await res.json();
      let changed = false;
      nodes.forEach(n => {
        if (!activeNodesMap[n.camera_id]) {
          activeNodesMap[n.camera_id] = n;
          changed = true;
        } else {
          const prev = activeNodesMap[n.camera_id];
          if (prev.online !== n.online || (!prev.latest_thumbnail && n.latest_thumbnail)) {
            changed = true;
          }
          Object.assign(prev, n);
        }
      });
      if (changed) {
        renderTopologyMap();
        renderCameraMatrix();
      }
    } catch (e) {
      console.warn('Could not fetch connected nodes:', e);
    }
  }

  function isNodeStreaming(node) {
    if (!node || node.online === false) return false;
    const fps = Number(node.fps || 0);
    if (fps > 0) return true;
    if (node.latest_thumbnail) {
      if (node.lastSeenLive && (Date.now() - node.lastSeenLive > 12000)) {
        return false;
      }
      return true;
    }
    return false;
  }

  function renderCameraMatrix() {
    const rawNodes = Object.values(activeNodesMap);
    const online = rawNodes.filter(n => n.online !== false);

    statActiveNodes.textContent = online.length;
    dashNodeCount.textContent = `${online.length} Camera${online.length === 1 ? '' : 's'} Online`;

    if (rawNodes.length === 0) {
      dashCameraMatrix.innerHTML = `
        <div class="empty-matrix-msg">
          No camera nodes active. Open the <strong>Camera Station</strong> to start streaming.
        </div>
      `;
      return;
    }

    // Sort: Live streaming cameras first, then online nodes, higher FPS, most recent heartbeat, camera ID
    const nodes = [...rawNodes].sort((a, b) => {
      const aLive = isNodeStreaming(a);
      const bLive = isNodeStreaming(b);
      if (aLive !== bLive) return bLive ? 1 : -1;

      const aOnline = a.online !== false;
      const bOnline = b.online !== false;
      if (aOnline !== bOnline) return bOnline ? 1 : -1;

      const aFps = Number(a.fps || 0);
      const bFps = Number(b.fps || 0);
      if (bFps !== aFps) return bFps - aFps;

      const aTime = Number(a.last_heartbeat || a.lastSeenLive || 0);
      const bTime = Number(b.last_heartbeat || b.lastSeenLive || 0);
      if (bTime !== aTime) return bTime - aTime;

      return (a.camera_id || '').localeCompare(b.camera_id || '');
    });

    let html = '';
    nodes.forEach(node => {
      const isOnline = node.online !== false;
      const isStreaming = isNodeStreaming(node);
      const thumb = node.latest_thumbnail;
      const thumbHtml = thumb
        ? `<img class="dash-cam-img" src="${thumb}" alt="${node.camera_id}">`
        : `<img class="dash-cam-img" style="display:none;" alt="${node.camera_id}"><div class="thumb-placeholder">Awaiting Video Feed...</div>`;

      let fpsText = 'OFFLINE';
      let fpsClass = '';
      if (isStreaming) {
        fpsText = `● LIVE · ${Number(node.fps || 0).toFixed(1)} FPS`;
        fpsClass = 'pill-green';
      } else if (isOnline) {
        fpsText = `STANDBY · 0.0 FPS`;
        fpsClass = '';
      }

      const cardStateClass = isStreaming ? 'online is-streaming' : (isOnline ? 'online' : 'offline');

      html += `
        <div id="cam-card-${node.camera_id}" class="dash-cam-card ${cardStateClass}">
          <div class="dash-cam-header">
            <div>
              <span class="dash-cam-id">${node.camera_id}</span>
              <span class="dash-cam-loc">(${node.location || 'Main Station'})</span>
            </div>
            <span class="pill-badge badge-fps ${fpsClass}">${fpsText}</span>
          </div>
          <div class="dash-cam-viewport">
            ${thumbHtml}
          </div>
        </div>
      `;
    });

    dashCameraMatrix.innerHTML = html;
  }

  // ---------------------------------------------------------------------------
  // 5. Stats & Sightings Logs
  // ---------------------------------------------------------------------------
  async function fetchStats() {
    try {
      const res = await fetch('/api/dashboard-stats');
      if (!res.ok) return;
      const stats = await res.json();
      statActiveNodes.textContent = stats.active_cameras || 0;
      statUniqueVerified.textContent = stats.unique_verified || 0;
      statTotalSightings.textContent = stats.total_sightings || 0;

      const navNodeBadge = document.getElementById('navNodeBadge');
      if (navNodeBadge) navNodeBadge.textContent = stats.active_cameras || 0;
      const navVerifiedBadge = document.getElementById('navVerifiedBadge');
      if (navVerifiedBadge) navVerifiedBadge.textContent = stats.unique_verified || 0;
    } catch (e) {
      console.warn('Could not fetch dashboard stats:', e);
    }
  }

  function formatTime(isoStr) {
    if (!isoStr) return '--:--:--';
    try {
      if (isoStr.includes('T')) {
        const timePart = isoStr.split('T')[1];
        return timePart.split('.')[0];
      }
      return isoStr;
    } catch (e) {
      return isoStr;
    }
  }

  async function fetchEvents() {
    const filter = filterStudentId.value.trim();
    const isDedup = currentTab === 'unique';
    let url = `/api/central-events?limit=50&deduplicate=${isDedup}`;
    if (filter) {
      url += `&student_id=${encodeURIComponent(filter)}`;
    }

    try {
      const res = await fetch(url);
      if (!res.ok) return;
      const events = await res.json();

      if (isDedup) {
        renderUniqueTable(events);
      } else {
        renderAllEventsTable(events);
      }
    } catch (e) {
      console.warn('Could not fetch events:', e);
    }
  }

  function renderUniqueTable(students) {
    if (!students || students.length === 0) {
      dashUniqueTableBody.innerHTML = `
        <tr class="empty-row">
          <td colspan="8">No verified students found matching the criteria.</td>
        </tr>
      `;
      return;
    }

    let html = '';
    students.forEach(s => {
      const firstSeen = formatTime(s.first_seen);
      const lastSeen = formatTime(s.last_seen);
      const count = s.sightings_count || 1;

      html += `
        <tr>
          <td><strong class="code-id">${s.student_id}</strong></td>
          <td><strong>${s.student_name || 'Authorized Student'}</strong></td>
          <td><span class="badge-cam">${s.camera_id}</span></td>
          <td>${s.camera_location || 'Main Station'}</td>
          <td><code>${firstSeen}</code></td>
          <td><code>${lastSeen}</code></td>
          <td><span class="pill-badge pill-cyan">${count} time${count === 1 ? '' : 's'}</span></td>
          <td><span class="tag-status tag-verified">VERIFIED</span></td>
        </tr>
      `;
    });

    dashUniqueTableBody.innerHTML = html;
  }

  function renderAllEventsTable(events) {
    if (!events || events.length === 0) {
      dashAllEventsTableBody.innerHTML = `
        <tr class="empty-row">
          <td colspan="7">No sighting events recorded.</td>
        </tr>
      `;
      return;
    }

    let html = '';
    events.forEach(e => {
      const timeStr = formatTime(e.observed_at);
      const confPct = Math.round((e.confidence || 0) * 100);
      const isVerified = e.status === 'VERIFIED';

      html += `
        <tr>
          <td><code>${timeStr}</code></td>
          <td><strong class="code-id">${e.student_id}</strong></td>
          <td>${e.student_name || 'Unknown'}</td>
          <td><span class="badge-cam">${e.camera_id}</span></td>
          <td>${e.camera_location || 'Main Station'}</td>
          <td><strong>${confPct}%</strong></td>
          <td>
            <span class="tag-status ${isVerified ? 'tag-verified' : 'tag-pending'}">
              ${e.status}
            </span>
          </td>
        </tr>
      `;
    });

    dashAllEventsTableBody.innerHTML = html;
  }

  // ---------------------------------------------------------------------------
  // 6. Enrolled Students Biometric Directory
  // ---------------------------------------------------------------------------
  async function fetchEnrolledStudents() {
    try {
      const res = await fetch('/api/node-info');
      if (!res.ok) return;
      const data = await res.json();
      const list = data.enrolled_students || [];
      const count = data.enrolled_count !== undefined ? data.enrolled_count : list.length;

      const dashEnrolledBadge = document.getElementById('dashEnrolledBadge');
      if (dashEnrolledBadge) dashEnrolledBadge.textContent = `${count} Students Enrolled`;

      const navEnrolledBadge = document.getElementById('navEnrolledBadge');
      if (navEnrolledBadge) navEnrolledBadge.textContent = count;

      renderEnrolledGrid(list);
    } catch (e) {
      console.warn('Could not fetch enrolled students:', e);
    }
  }

  function renderEnrolledGrid(students) {
    const grid = document.getElementById('dashEnrolledGrid');
    if (!grid) return;

    if (!students || students.length === 0) {
      grid.innerHTML = '<div class="empty-matrix-msg">No students registered yet in biometric directory.</div>';
      return;
    }

    grid.innerHTML = students.map(s => `
      <div class="student-card">
        <div class="student-avatar">👤</div>
        <div class="student-info">
          <h4>${s.name || s.student_id}</h4>
          <div class="student-id-tag">ID: ${s.student_id}</div>
          <div class="student-meta">${s.department || 'Engineering'} • Batch ${s.batch || '2026'}</div>
        </div>
      </div>
    `).join('');
  }

  // ---------------------------------------------------------------------------
  // 7. Sidebar Navigation & Tabbed View Switching
  // ---------------------------------------------------------------------------
  const viewTitles = {
    'topology': 'Real-Time Surveillance & Attendance Matrix',
    'attendance': 'Student Attendance Verification & Activity Audit Logs',
    'enrolled': 'Enrolled Student Biometrics Directory'
  };

  window.switchDashboardView = function (viewName) {
    const panels = document.querySelectorAll('.dash-view-panel');
    const links = document.querySelectorAll('.sidebar-nav .nav-link[data-view]');
    const topbarTitle = document.getElementById('topbarViewTitle');

    // 1. Update active sidebar link
    links.forEach(l => {
      if (l.getAttribute('data-view') === viewName) {
        l.classList.add('active');
      } else {
        l.classList.remove('active');
      }
    });

    // 2. Show only the targeted view panel, hide all others
    let found = false;
    panels.forEach(p => {
      if (p.id === `view-${viewName}`) {
        p.style.display = 'flex';
        p.classList.add('active');
        found = true;
      } else {
        p.style.display = 'none';
        p.classList.remove('active');
      }
    });

    // Fallback if not found: show topology
    if (!found && panels.length > 0) {
      panels[0].style.display = 'flex';
      panels[0].classList.add('active');
    }

    // 3. Update topbar breadcrumb view title
    if (topbarTitle && viewTitles[viewName]) {
      topbarTitle.textContent = viewTitles[viewName];
    }

    // 4. Update URL hash
    if (history.replaceState) {
      history.replaceState(null, null, `#${viewName}`);
    }

    // 5. Trigger view-specific data updates
    if (viewName === 'topology') {
      setTimeout(() => {
        renderTopologyMap();
        renderCameraMatrix();
      }, 50);
    } else if (viewName === 'attendance') {
      fetchEvents();
    } else if (viewName === 'enrolled') {
      fetchEnrolledStudents();
    }

    // Close mobile sidebar if open
    const dashSidebar = document.getElementById('dashSidebar');
    if (dashSidebar && dashSidebar.classList.contains('sidebar-open')) {
      dashSidebar.classList.remove('sidebar-open');
    }

    // Scroll content area back to top
    const contentArea = document.querySelector('.dash-content-area');
    if (contentArea) {
      contentArea.scrollTop = 0;
    }
  };

  function setupSidebarAndNav() {
    const btnSidebarToggle = document.getElementById('btnSidebarToggle');
    const dashSidebar = document.getElementById('dashSidebar');
    if (btnSidebarToggle && dashSidebar) {
      btnSidebarToggle.addEventListener('click', () => {
        dashSidebar.classList.toggle('sidebar-open');
      });
      document.addEventListener('click', (e) => {
        if (dashSidebar.classList.contains('sidebar-open') &&
            !dashSidebar.contains(e.target) &&
            e.target !== btnSidebarToggle) {
          dashSidebar.classList.remove('sidebar-open');
        }
      });
    }

    const navLinks = document.querySelectorAll('.dash-sidebar .nav-link[data-view]');
    navLinks.forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const v = link.getAttribute('data-view');
        if (v) window.switchDashboardView(v);
      });
    });

    // Check URL hash on initial load
    const hash = window.location.hash.replace('#', '');
    if (hash && ['topology', 'attendance', 'enrolled'].includes(hash)) {
      window.switchDashboardView(hash);
    } else {
      window.switchDashboardView('topology');
    }
  }

  function setupModal() {
    // Modal open/close is harmonized with app.js
  }

  // Self-start
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
