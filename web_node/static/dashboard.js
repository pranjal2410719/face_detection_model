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
    await fetchStats();
    await fetchConnectedNodes();
    await fetchTrajectories();
    await fetchEvents();
    connectDashboardWebSocket();

    // Auto-refresh interval (every 4s)
    setInterval(() => {
      fetchStats();
      fetchConnectedNodes();
      fetchEvents();
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
        } else if (msg.type === 'NODE_OFFLINE') {
          if (activeNodesMap[msg.data.camera_id]) {
            activeNodesMap[msg.data.camera_id].online = false;
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
    activeNodesMap[camId] = Object.assign(activeNodesMap[camId] || {}, data);

    if (isNew) {
      renderTopologyMap();
      renderCameraMatrix();
      return;
    }

    // 2. If face detection is active on this frame, update node confidence state
    if (data.active_faces > 0 && data.latest_confidence > 0) {
      updateNodeDetection(camId, {
        confidence: data.latest_confidence,
        studentName: data.latest_name || 'Authorized Student',
        studentId: data.latest_student || '',
        status: data.latest_status || 'VERIFIED'
      });
    }

    // 3. Update Camera Card (without DOM flickering)
    const card = document.getElementById(`cam-card-${camId}`);
    if (card) {
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
        fpsBadge.textContent = `● LIVE · ${Number(data.fps || 0).toFixed(1)} FPS`;
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
    const conf = (sighting.confidence || 75) / 100.0;

    // Trigger detection color change on node
    if (camId) {
      updateNodeDetection(camId, {
        confidence: conf,
        studentName: stuName,
        studentId: stuId,
        status: sighting.status || 'VERIFIED'
      });
    }

    // Check cross-camera transition
    const prevCam = studentLastCamera[stuId];
    if (prevCam && prevCam !== camId && activeNodesMap[prevCam]) {
      addTransitionEdge(prevCam, camId, stuId, stuName);
    }
    studentLastCamera[stuId] = camId;
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

  function updateNodeDetection(camId, info) {
    nodeDetectionState[camId] = {
      confidence: info.confidence,
      studentName: info.studentName,
      studentId: info.studentId,
      status: info.status,
      lastSeenTime: Date.now()
    };

    // Apply color change to SVG node directly
    const nodeEl = document.getElementById(`topo-node-wrap-${camId}`);
    if (nodeEl) {
      const shade = getConfidenceShade(info.confidence);
      const circleEl = nodeEl.querySelector('.node-center-circle');
      const pulseEl = nodeEl.querySelector('.node-pulse-ring');
      const badgeText = nodeEl.querySelector('.node-badge-text');
      const badgeWrap = nodeEl.querySelector('.node-detect-badge');

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

      if (badgeWrap && badgeText) {
        badgeText.textContent = `${info.studentName} [${Math.round(info.confidence * 100)}%]`;
        badgeWrap.style.display = 'block';
      }
    }

    topoTransitionTicker.innerHTML = `
      <span class="ticker-highlight">● Sighting:</span> 
      <strong>${info.studentName}</strong> detected at <code>${camId}</code> with <strong>${Math.round(info.confidence * 100)}%</strong> confidence.
    `;
  }

  function decayNodeDetections() {
    const now = Date.now();
    let updatedAny = false;

    for (const camId in nodeDetectionState) {
      const state = nodeDetectionState[camId];
      if (state && (now - state.lastSeenTime > 6000)) {
        // Return back to standby gray
        delete nodeDetectionState[camId];
        const nodeEl = document.getElementById(`topo-node-wrap-${camId}`);
        if (nodeEl) {
          const shade = getConfidenceShade(0);
          const circleEl = nodeEl.querySelector('.node-center-circle');
          const pulseEl = nodeEl.querySelector('.node-pulse-ring');
          const badgeWrap = nodeEl.querySelector('.node-detect-badge');

          if (circleEl) {
            circleEl.setAttribute('fill', shade.fill);
            circleEl.setAttribute('stroke', shade.stroke);
            circleEl.removeAttribute('filter');
          }
          if (pulseEl) pulseEl.style.display = 'none';
          if (badgeWrap) badgeWrap.style.display = 'none';
        }
        updatedAny = true;
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

  function addTransitionEdge(fromCam, toCam, studentId, studentName) {
    // Add to active transition edges
    activeTransitions.push({
      from: fromCam,
      to: toCam,
      studentId: studentId,
      studentName: studentName,
      timestamp: Date.now()
    });

    renderEdges();

    // Update status ticker
    topoTransitionTicker.innerHTML = `
      <span class="ticker-transition-pill">➔ TRAJECTORY:</span>
      <strong>${studentName}</strong> moved from <code>${fromCam}</code> ➔ <code>${toCam}</code>!
    `;
  }

  function calculateNodePositions(nodes) {
    const count = nodes.length;
    nodeCoordinates = {};

    if (count === 0) return;

    if (count === 1) {
      nodeCoordinates[nodes[0].camera_id] = { x: 500, y: 170 };
      return;
    }

    if (count === 2) {
      nodeCoordinates[nodes[0].camera_id] = { x: 280, y: 170 };
      nodeCoordinates[nodes[1].camera_id] = { x: 720, y: 170 };
      return;
    }

    if (count === 3) {
      nodeCoordinates[nodes[0].camera_id] = { x: 220, y: 170 };
      nodeCoordinates[nodes[1].camera_id] = { x: 500, y: 130 };
      nodeCoordinates[nodes[2].camera_id] = { x: 780, y: 170 };
      return;
    }

    // N nodes: evenly spaced with subtle alternating height
    const padX = 140;
    const step = (1000 - 2 * padX) / Math.max(1, count - 1);
    nodes.forEach((n, idx) => {
      const x = padX + idx * step;
      const y = 170 + (idx % 2 === 0 ? -30 : 30);
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

    // 1. Render Nodes
    let nodesHtml = '';
    nodes.forEach(node => {
      const pos = nodeCoordinates[node.camera_id] || { x: 500, y: 170 };
      const state = nodeDetectionState[node.camera_id] || {};
      const shade = getConfidenceShade(state.confidence);
      const isDetected = state.confidence !== undefined && state.confidence > 0;
      const badgeStyle = isDetected ? 'display: block;' : 'display: none;';
      const badgeText = isDetected ? `${state.studentName} [${Math.round(state.confidence * 100)}%]` : '';
      const filterAttr = shade.filter !== 'none' ? `filter="${shade.filter}"` : '';

      nodesHtml += `
        <g id="topo-node-wrap-${node.camera_id}" class="topo-node-group" transform="translate(${pos.x}, ${pos.y})">
          <!-- Animated Radar Wave (When Face is Detected) -->
          <circle class="node-pulse-ring" r="38" stroke="${shade.stroke}" stroke-width="2" fill="none" style="${isDetected ? 'display:block;' : 'display:none;'}"></circle>
          
          <!-- Outer Ring -->
          <circle class="node-outer-ring" r="32" stroke="rgba(255, 255, 255, 0.15)" stroke-width="1.5" fill="none"></circle>
          
          <!-- Core Center Circle (Changes Gray -> Green -> Greener) -->
          <circle class="node-center-circle" r="26" fill="${shade.fill}" stroke="${shade.stroke}" stroke-width="2.5" ${filterAttr}></circle>
          
          <!-- Camera Glyphs -->
          <text class="node-icon" y="6" text-anchor="middle" font-size="14" fill="#ffffff">📹</text>
          
          <!-- Node ID Label -->
          <text class="node-label-id" y="48" text-anchor="middle">${node.camera_id}</text>
          
          <!-- Node Location Sub-label -->
          <text class="node-label-loc" y="62" text-anchor="middle">${node.location || 'Camera Station'}</text>

          <!-- Floating Detection Badge -->
          <g class="node-detect-badge" transform="translate(0, -42)" style="${badgeStyle}">
            <rect x="-80" y="-12" width="160" height="24" rx="12" fill="#0f172a" stroke="${shade.stroke}" stroke-width="1.5"></rect>
            <text class="node-badge-text" text-anchor="middle" y="4" font-size="11" font-weight="700" fill="#00ff88">${badgeText}</text>
          </g>
        </g>
      `;
    });

    topoNodesLayer.innerHTML = nodesHtml;

    // 2. Render Existing Edges
    renderEdges();
  }

  function renderEdges() {
    let edgesHtml = '';

    activeTransitions.forEach(trans => {
      const p1 = nodeCoordinates[trans.from];
      const p2 = nodeCoordinates[trans.to];

      if (!p1 || !p2) return;

      // Compute trimmed arrow endpoints
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dist < 10) return;

      const rStart = 34;
      const rEnd = 38;

      const x1 = p1.x + (dx * rStart) / dist;
      const y1 = p1.y + (dy * rStart) / dist;
      const x2 = p2.x - (dx * rEnd) / dist;
      const y2 = p2.y - (dy * rEnd) / dist;

      // Arch control point for beautiful curved trajectory
      const midX = (x1 + x2) / 2;
      const midY = (y1 + y2) / 2 - 35;

      edgesHtml += `
        <g class="edge-group">
          <!-- Flowing Glowing Edge Path -->
          <path d="M ${x1} ${y1} Q ${midX} ${midY} ${x2} ${y2}" 
                class="topo-edge-path" 
                marker-end="url(#edgeArrowGreen)"></path>

          <!-- Floating Movement Tag on Edge -->
          <g transform="translate(${midX}, ${midY - 10})">
            <rect x="-70" y="-10" width="140" height="20" rx="4" fill="rgba(6, 95, 70, 0.9)" stroke="#00ff88" stroke-width="1"></rect>
            <text text-anchor="middle" y="4" font-size="10" font-family="'JetBrains Mono', monospace" font-weight="700" fill="#ffffff">
              ➔ ${trans.studentName || trans.studentId}
            </text>
          </g>
        </g>
      `;
    });

    topoEdgesLayer.innerHTML = edgesHtml;
  }

  async function fetchTrajectories() {
    try {
      const res = await fetch('/api/trajectories');
      if (!res.ok) return;
      const trajs = await res.json();
      trajs.forEach(t => {
        if (t.student_id && t.current_camera) {
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

  function renderCameraMatrix() {
    const nodes = Object.values(activeNodesMap);
    const online = nodes.filter(n => n.online);

    statActiveNodes.textContent = online.length;
    dashNodeCount.textContent = `${online.length} Camera${online.length === 1 ? '' : 's'} Online`;

    if (nodes.length === 0) {
      dashCameraMatrix.innerHTML = `
        <div class="empty-matrix-msg">
          No camera nodes active. Open the <strong>Camera Station</strong> to start streaming.
        </div>
      `;
      return;
    }

    let html = '';
    nodes.forEach(node => {
      const isOnline = node.online !== false;
      const thumb = node.latest_thumbnail;
      const thumbHtml = thumb
        ? `<img class="dash-cam-img" src="${thumb}" alt="${node.camera_id}">`
        : `<img class="dash-cam-img" style="display:none;" alt="${node.camera_id}"><div class="thumb-placeholder">Awaiting Video Feed...</div>`;

      const fpsText = isOnline ? `● LIVE · ${Number(node.fps || 0).toFixed(1)} FPS` : 'OFFLINE';

      html += `
        <div id="cam-card-${node.camera_id}" class="dash-cam-card ${isOnline ? 'online' : 'offline'}">
          <div class="dash-cam-header">
            <div>
              <span class="dash-cam-id">${node.camera_id}</span>
              <span class="dash-cam-loc">(${node.location || 'Main Station'})</span>
            </div>
            <span class="pill-badge badge-fps ${isOnline ? 'pill-green' : ''}">${fpsText}</span>
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

  // Self-start
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
