import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import * as THREE from 'three';
import {GLTFLoader} from 'three/examples/jsm/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/examples/jsm/controls/OrbitControls.js';
import {Activity, Box, Radio, ShieldAlert, CircleHelp} from 'lucide-react';
import './style.css';

const MODEL_URL = '/3d/models/city-cs-challenge.glb';
const MARKERS = [
  {id: 'S-WATER-01', label: 'Water sensor', zone_id: 'Z-RIVER', position: {x: 0.52, y: 0.12, z: 0.48}},
  {id: 'DTN-VLV-01', label: 'Valve actuator · V-12', zone_id: 'Z-DOWNTOWN', position: {x: 0.68, y: 0.08, z: 0.55}}
];
const DOMAINS = [
  {id: 'Z-RIVER', label: 'Port', note: 'Mapped to CityCore River zone'},
  {id: 'Z-INDUSTRIAL', label: 'Industrial'},
  {id: 'Z-DOWNTOWN', label: 'Downtown'},
  {id: 'Z-RESIDENTIAL', label: 'Residential'},
  {id: 'Z-HEALTH', label: 'Health'}
];
const ZONE_LABELS = Object.fromEntries([
  ...DOMAINS.map(domain => [domain.id, domain.label]),
  ['PRT', 'Port · River zone'],
  ['IND', 'Industrial'],
  ['DTN', 'Downtown'],
  ['RES', 'Residential'],
  ['HSP', 'Health']
]);
const DOMAIN_ZONE = {'PRT': 'Z-RIVER', 'IND': 'Z-INDUSTRIAL', 'DTN': 'Z-DOWNTOWN',
  'RES': 'Z-RESIDENTIAL', 'HSP': 'Z-HEALTH', 'Z-HEALTH': 'Z-HEALTH'};

async function requestJSON(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    cache: 'no-store',
    credentials: 'same-origin',
    redirect: 'error',
    ...(body === undefined ? {} : {
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    })
  });
  const value = await response.json();
  if (!response.ok || value.ok !== true) {
    throw new Error(value.error?.code || 'REQUEST_FAILED');
  }
  return value.data;
}

async function requestHealth() {
  const response = await fetch('/health/ready', {cache: 'no-store', credentials: 'same-origin'});
  const result = await response.json();
  if (!response.ok || typeof result.status !== 'string') {
    throw new Error('HEALTH_UNAVAILABLE');
  }
  return result;
}

function latestBySensor(rows) {
  const latest = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row.sensor_id !== 'string') continue;
    const previous = latest.get(row.sensor_id);
    const tick = row.delivery_tick ?? row.sim_tick ?? -1;
    const previousTick = previous?.delivery_tick ?? previous?.sim_tick ?? -1;
    if (!previous || tick > previousTick || (tick === previousTick && (row.seq ?? 0) > (previous.seq ?? 0))) {
      latest.set(row.sensor_id, row);
    }
  }
  return latest;
}

function sensorRows(frame) {
  if (!frame) return [];
  const privateView = frame.visibility === 'PRIVATE_OPERATOR_ONLY';
  const readings = latestBySensor(privateView ? frame.readings : frame.records);
  const comparisons = latestBySensor(privateView ? frame.comparisons : []);
  const registry = privateView && Array.isArray(frame.sensors) ? frame.sensors : [];
  const ids = new Set([...registry.map(row => row.sensor_id), ...readings.keys()]);
  return [...ids].sort().map(sensorId => {
    const sensor = registry.find(row => row.sensor_id === sensorId);
    const observation = readings.get(sensorId) || null;
    const comparison = comparisons.get(sensorId) || null;
    return {
      sensor_id: sensorId,
      zone_id: sensor?.zone_id || observation?.zone_id || comparison?.zone_id || null,
      type: sensor?.type || observation?.type || comparison?.type || 'Unknown',
      unit: sensor?.unit || observation?.unit || comparison?.unit || '',
      active: sensor?.active ?? true,
      source_bound: privateView ? Boolean(sensor?.source) : null,
      actual_value: privateView && Number.isFinite(comparison?.actual_value) ? comparison.actual_value : null,
      reported_value: Number.isFinite(observation?.reported_value) ? observation.reported_value
        : Number.isFinite(comparison?.reported_value) ? comparison.reported_value : null,
      delta: privateView && Number.isFinite(comparison?.delta) ? Math.abs(comparison.delta) : null,
      status: observation?.status || comparison?.status || 'UNKNOWN',
      available: observation?.available ?? comparison?.available ?? null,
      timestamp: observation?.timestamp || comparison?.timestamp || null,
      sim_tick: observation?.sim_tick ?? comparison?.sim_tick ?? null,
      delivery_tick: observation?.delivery_tick ?? comparison?.delivery_tick ?? null,
      linked_device_ids: sensor?.linked_device_ids || observation?.linked_device_ids || [],
      comparison_available: privateView && Number.isFinite(comparison?.actual_value) &&
        Number.isFinite(comparison?.reported_value)
    };
  });
}

function domainName(zoneId) {
  if (!zoneId) return 'Unknown';
  return ZONE_LABELS[zoneId] || zoneId;
}

function comparisonStatus(row, privateView) {
  if (!privateView) return 'PRIVATE';
  if (row.available === false || row.status === 'OFFLINE') return 'OFFLINE';
  if (!row.comparison_available) return row.status === 'UNKNOWN' ? 'UNKNOWN' : row.status;
  return row.delta > 0.01 ? 'MISMATCH' : 'NORMAL';
}

function latestSyncText(value) {
  return value ? new Date(value).toLocaleTimeString() : 'Not synchronized';
}

function ShowMore({expanded, total, label, onClick}) {
  if (total <= 6) return null;
  return <button className="show-more" type="button" aria-expanded={expanded} onClick={onClick}>
    {expanded ? `Show fewer ${label}` : `Show all ${total} ${label}`}
  </button>;
}

function formatValue(value, unit) {
  return Number.isFinite(value) ? `${Number(value.toFixed(1))}${unit ? ` ${unit}` : ''}` : 'Not available';
}

function Viewer({reading, valveFraction, sensorStatus, selectedDomain, onSelectMarker}) {
  const mount = useRef(null);
  const markerMeshes = useRef(new Map());
  const cameraRef = useRef(null);
  const [modelMessage, setModelMessage] = useState('Loading city model (34 MB)…');

  useEffect(() => {
    const host = mount.current;
    if (!host) return undefined;
    let renderer;
    let controls;
    let resizeObserver;
    let animation;
    let disposed = false;
    let modelScene;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color('#0b1220');
    const camera = new THREE.PerspectiveCamera(45, Math.max(host.clientWidth, 1) / Math.max(host.clientHeight, 1), .01, 100000);
    camera.position.set(2, 2, 3);
    cameraRef.current = camera;

    try {
      renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: 'low-power'});
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(host.clientWidth, host.clientHeight);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      host.appendChild(renderer.domElement);
    } catch {
      setModelMessage('3D rendering is unavailable in this browser.');
      return undefined;
    }

    scene.add(new THREE.HemisphereLight(0xffffff, 0x526078, 2));
    const light = new THREE.DirectionalLight(0xffffff, 2);
    light.position.set(5, 10, 7);
    scene.add(light);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const grid = new THREE.GridHelper(10, 20, 0x34445e, 0x1b2b42);
    scene.add(grid);

    const loader = new GLTFLoader();
    loader.load(MODEL_URL, gltf => {
      if (disposed) return;
      modelScene = gltf.scene;
      scene.add(modelScene);
      const bounds = new THREE.Box3().setFromObject(modelScene);
      const size = bounds.getSize(new THREE.Vector3());
      const center = bounds.getCenter(new THREE.Vector3());
      modelScene.position.sub(center);
      const scale = Math.max(size.x, size.y, size.z) || 1;
      camera.position.set(scale * 1.4, scale * 1.1, scale * 1.7);
      camera.near = Math.max(scale / 10000, .001);
      camera.far = scale * 100;
      camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      controls.update();

      const markerMap = new Map();
      for (const marker of MARKERS) {
        const point = new THREE.Vector3(
          bounds.min.x + size.x * marker.position.x,
          bounds.min.y + size.y * marker.position.y,
          bounds.min.z + size.z * marker.position.z
        ).sub(center);
        const group = new THREE.Group();
        group.position.copy(point);
        group.userData.markerId = marker.id;
        group.userData.zoneId = marker.zone_id;
        const sphere = new THREE.Mesh(
          new THREE.SphereGeometry(scale * .012, 20, 20),
          new THREE.MeshStandardMaterial({color: '#8793a3', emissive: '#8793a3', emissiveIntensity: .5})
        );
        sphere.userData.markerId = marker.id;
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(scale * .018, scale * .025, 32),
          new THREE.MeshBasicMaterial({color: '#8793a3', side: THREE.DoubleSide})
        );
        ring.rotation.x = -Math.PI / 2;
        ring.position.y = -scale * .018;
        group.add(sphere, ring);
        scene.add(group);
        markerMap.set(marker.id, group);
      }
      markerMeshes.current = markerMap;
      grid.scale.setScalar(scale / 10);
      grid.position.y = -size.y / 2;
      setModelMessage('City model loaded · drag to orbit · scroll to zoom · right-drag to pan.');
    }, progress => {
      if (!disposed && progress.total > 0) {
        setModelMessage(`Loading city model: ${Math.round(progress.loaded / progress.total * 100)}%.`);
      }
    }, () => {
      if (!disposed) setModelMessage('City model could not be loaded. Check the local model asset and reload.');
    });

    const draw = () => {
      controls.update();
      renderer.render(scene, camera);
      animation = requestAnimationFrame(draw);
    };
    draw();
    resizeObserver = new ResizeObserver(() => {
      if (!host.clientWidth || !host.clientHeight) return;
      camera.aspect = host.clientWidth / host.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(host.clientWidth, host.clientHeight);
    });
    resizeObserver.observe(host);

    const fit = () => {
      const modelBounds = modelScene && new THREE.Box3().setFromObject(modelScene);
      if (!modelBounds) return;
      const size = modelBounds.getSize(new THREE.Vector3());
      const distance = Math.max(size.x, size.y, size.z) * 1.8;
      camera.position.set(distance, distance * .75, distance);
      controls.target.set(0, 0, 0);
      controls.update();
    };
    const focus = event => {
      const marker = markerMeshes.current.get(event.detail);
      if (!marker) return;
      controls.target.copy(marker.position);
      controls.update();
    };
    window.addEventListener('city-model-fit', fit);
    window.addEventListener('city-model-focus', focus);

    return () => {
      disposed = true;
      cancelAnimationFrame(animation);
      resizeObserver?.disconnect();
      window.removeEventListener('city-model-fit', fit);
      window.removeEventListener('city-model-focus', focus);
      controls.dispose();
      scene.traverse(object => {
        object.geometry?.dispose();
        if (Array.isArray(object.material)) object.material.forEach(material => material.dispose());
        else object.material?.dispose();
      });
      renderer.dispose();
      renderer.domElement.remove();
      markerMeshes.current.clear();
      cameraRef.current = null;
    };
  }, []);

  useEffect(() => {
    const sensor = markerMeshes.current.get('S-WATER-01');
    if (sensor) {
      const color = sensorStatus === 'MISMATCH' ? '#f5bd62'
        : sensorStatus === 'OFFLINE' ? '#ff536e'
          : sensorStatus === 'NORMAL' ? '#42d6a4' : '#8793a3';
      sensor.children[0].material.color.set(color);
      sensor.children[0].material.emissive.set(color);
    }
    const valve = markerMeshes.current.get('DTN-VLV-01');
    if (valve) {
      const color = Number.isFinite(valveFraction) && valveFraction <= 0 ? '#ff536e' : '#f6bd60';
      valve.children[0].material.color.set(color);
      valve.children[0].material.emissive.set(color);
    }
    for (const marker of markerMeshes.current.values()) {
      marker.visible = !selectedDomain || marker.userData.zoneId === selectedDomain;
    }
  }, [reading, valveFraction, sensorStatus, selectedDomain]);

  const selectMarker = event => {
    const rect = event.currentTarget.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(pointer, cameraRef.current);
    const hit = raycaster.intersectObjects([...markerMeshes.current.values()], true)[0];
    if (!hit) return;
    const id = hit.object.userData.markerId;
    if (id) {
      window.dispatchEvent(new CustomEvent('city-model-focus', {detail: id}));
      onSelectMarker(id);
    }
  };

  return <div className="scene-panel">
    <div className="viewer" ref={mount} onClick={selectMarker}>
      <div className="viewer-tag"><Box size={14}/> CITY MODEL · GLB</div>
      <div className="viewer-help">{modelMessage}</div>
      <button className="viewer-fit" type="button" aria-label="Fit city model" onClick={event => {
        event.stopPropagation();
        window.dispatchEvent(new Event('city-model-fit'));
      }}>Fit</button>
    </div>
  </div>;
}

function actuatorStateText(actuator) {
  const state = actuator?.simulated_state;
  if (!state) return 'State unavailable';
  if (actuator.family === 'valve' && Number.isFinite(state.effective_opening_fraction)) {
    return `${Math.round(state.effective_opening_fraction * 100)}% effective opening`;
  }
  if (actuator.family === 'pump' && typeof state.effective_enabled === 'boolean') {
    return state.effective_enabled ? 'Enabled in simulation' : 'Disabled in simulation';
  }
  if (actuator.family === 'signal' && state.effective_mode) return state.effective_mode;
  if (actuator.family === 'feeder' && typeof state.effective_closed === 'boolean') {
    return state.effective_closed ? 'Closed in simulation' : 'Open in simulation';
  }
  if (actuator.family === 'responder' && state.status) return state.status;
  return 'State unavailable';
}

function eventDetails(event) {
  return event?.details && typeof event.details === 'object' ? event.details : {};
}

function eventLabel(event) {
  const details = eventDetails(event);
  return details.event_type || event?.type || 'EVENT';
}

function eventDescription(event) {
  const details = eventDetails(event);
  return details.message || details.action || details.device_id || details.sensor_id ||
    details.action_id || details.code || 'Twin event recorded';
}

function eventDomain(event) {
  const details = eventDetails(event);
  return domainName(event?.zone_id || details.zone_id || details.zone);
}

function App() {
  const [frameState, setFrameState] = useState({frame: null, stale: false, receivedAt: null});
  const [passphrase, setPassphrase] = useState('');
  const [error, setError] = useState('');
  const [health, setHealth] = useState(null);
  const [busy, setBusy] = useState(false);
  const [scenarioBusy, setScenarioBusy] = useState(false);
  const [scenarioChoice, setScenarioChoice] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [selectedDomain, setSelectedDomain] = useState('');
  const [selectedDevice, setSelectedDevice] = useState('');
  const [eventType, setEventType] = useState('ALL');
  const [eventDomainFilter, setEventDomainFilter] = useState('ALL');
  const [expandedLists, setExpandedLists] = useState({});
  const isExpanded = key => Boolean(expandedLists[key]);
  const toggleList = key => setExpandedLists(previous => ({...previous, [key]: !previous[key]}));
  const selectDevice = deviceId => setSelectedDevice(deviceId);
  const frame = frameState.frame;
  const isOperator = frame?.visibility === 'PRIVATE_OPERATOR_ONLY';
  const stale = frameState.stale;
  const rows = useMemo(() => sensorRows(frame), [frame]);
  const sensorMap = useMemo(() => new Map(rows.map(row => [row.sensor_id, row])), [rows]);
  const waterSensor = sensorMap.get('S-WATER-01') || null;
  const waterStatus = waterSensor ? comparisonStatus(waterSensor, isOperator) : 'UNKNOWN';
  const physical = isOperator ? frame?.diagnostics?.physical_state : null;
  const valveFraction = physical?.valve?.effective_opening_fraction;
  const actuatorRows = isOperator && Array.isArray(frame?.actuators) ? frame.actuators : [];
  const quarantines = isOperator && Array.isArray(frame?.security?.quarantined_devices)
    ? frame.security.quarantined_devices : [];
  const scenario = isOperator ? frame?.scenario : null;
  const scenarios = Array.isArray(frame?.scenario_catalog) ? frame.scenario_catalog : [];
  const selectedScenarioId = scenarios.some(item => item.scenario_id === scenarioChoice)
    ? scenarioChoice : scenarios[0]?.scenario_id || '';
  const actions = isOperator && Array.isArray(frame?.actions) ? frame.actions : [];
  const allEvents = isOperator && Array.isArray(frame?.events) ? frame.events
    : frame?.visibility === 'REPORTED_ONLY'
      ? (frame.records || []).map(row => ({type: 'SENSOR_OBSERVATION', timestamp: row.timestamp,
        sim_tick: row.sim_tick, device_id: row.sensor_id, zone_id: row.zone_id,
        details: {sensor_id: row.sensor_id, status: row.status, reported_value: row.reported_value, unit: row.unit}}))
      : [];
  const eventTypes = [...new Set(allEvents.map(eventLabel))].sort();
  const currentEvents = allEvents.filter(item => {
    const typeMatches = eventType === 'ALL' || eventLabel(item) === eventType;
    const zone = DOMAIN_ZONE[item?.zone_id] || item?.zone_id;
    return typeMatches && (eventDomainFilter === 'ALL' || zone === eventDomainFilter);
  });
  const pendingActions = actions.filter(action => action.status === 'PENDING').length;
  const mismatches = rows.filter(row => comparisonStatus(row, isOperator) === 'MISMATCH').length;
  const unavailableSensors = rows.filter(row => row.available === false || row.status === 'OFFLINE').length;
  const flaggedSensors = rows.filter(row => {
    const status = comparisonStatus(row, isOperator);
    return status !== 'NORMAL' && status !== 'PRIVATE' && status !== 'UNKNOWN';
  }).length;
  const activeScenario = scenario?.status === 'RUNNING';
  const runtimeStatus = isOperator ? frame?.status?.state : 'Not available in reported view';
  const connection = !frame ? 'DISCONNECTED' : stale ? 'STALE' : 'CONNECTED';
  const selectedZone = selectedDomain || '';
  const visibleRows = rows.filter(row => !selectedZone || DOMAIN_ZONE[row.zone_id] === selectedZone || row.zone_id === selectedZone);
  const visibleActuators = actuatorRows.filter(row => !selectedZone || DOMAIN_ZONE[row.zone_id] === selectedZone || row.core_zone_id === selectedZone);
  const sensorRegistry = isOperator && Array.isArray(frame?.sensors) ? frame.sensors : [];
  const selectedSensor = sensorMap.get(selectedDevice);
  const selectedHistory = isOperator && selectedDevice
    ? (frame?.readings || []).filter(row => row.sensor_id === selectedDevice).slice(-8).reverse()
    : [];
  const actionEvents = allEvents.filter(event => /ACTION_|COMMAND_/.test(eventLabel(event))).reverse();
  const comparisonRows = rows.map(row => {
    const state = comparisonStatus(row, isOperator);
    return <tr key={row.sensor_id} className={state === 'MISMATCH' ? 'row-warning' : state === 'OFFLINE' ? 'row-offline' : ''}>
      <td><b>{row.sensor_id}</b><small>{row.type} · {domainName(row.zone_id)}</small></td>
      <td>{isOperator ? formatValue(row.actual_value, row.unit) : 'PRIVATE'}</td>
      <td>{formatValue(row.reported_value, row.unit)}</td>
      <td>{isOperator ? formatValue(row.delta, row.unit) : 'PRIVATE'}</td>
      <td><span className={`pill state-${state.toLowerCase()}`}>{state}</span></td>
      <td>Not reported</td>
      <td>{row.sim_tick === null ? 'No sample' : `t=${row.sim_tick}${row.delivery_tick !== row.sim_tick ? ` · delivered t=${row.delivery_tick}` : ''}`}</td>
    </tr>;
  });
  const sensorTableRows = visibleRows.map(row => <tr key={row.sensor_id} className={selectedDevice === row.sensor_id ? 'row-selected' : ''}>
      <td><button className="text-button" type="button" onClick={() => selectDevice(row.sensor_id)}>{row.sensor_id}</button></td>
      <td>{row.type}<small>{domainName(row.zone_id)}</small></td>
      <td>{isOperator ? row.source_bound ? 'Source bound' : 'Unbound' : 'Not exposed'}</td>
      <td>Not reported</td>
      <td>{formatValue(row.reported_value, row.unit)}</td>
      <td>{isOperator ? formatValue(row.actual_value, row.unit) : 'Private'}</td>
      <td>{row.sim_tick === null ? 'No sample' : `t=${row.sim_tick}${row.delivery_tick !== row.sim_tick ? ` · delivered t=${row.delivery_tick}` : ''}`}<small>{row.timestamp || ''}</small></td>
      <td><span className={`pill state-${comparisonStatus(row, isOperator).toLowerCase()}`}>{comparisonStatus(row, isOperator)}</span></td>
    </tr>);
  const actuatorTableRows = visibleActuators.map(actuator => <tr key={`actuator-${actuator.device_id}`} className={selectedDevice === actuator.device_id ? 'row-selected' : ''}>
      <td><button className="text-button" type="button" onClick={() => selectDevice(actuator.device_id)}>{actuator.device_id}</button></td>
      <td>{actuator.type}<small>{domainName(actuator.core_zone_id || actuator.zone_id)}{actuator.target_id ? ` · target ${actuator.target_id}` : ''}</small></td>
      <td>{actuator.supported ? 'Supported in simulation' : 'Unsupported'}</td>
      <td>Physical link not reported</td>
      <td>Not applicable</td>
      <td>{actuatorStateText(actuator)}</td>
      <td>Current frame · t={frame.sim_tick ?? '—'}<small>Simulation state only</small></td>
      <td><span className={`pill ${actuator.supported ? 'state-normal' : 'state-unknown'}`}>{actuator.supported ? 'SIMULATED' : 'UNSUPPORTED'}</span></td>
    </tr>);
  const deviceTableRows = [...sensorTableRows, ...actuatorTableRows];
  const devicePreviewRows = [...sensorTableRows.slice(0, 4), ...actuatorTableRows.slice(0, 2)];
  const eventRows = [...currentEvents].reverse();
  const scenarioRows = isExpanded('scenarios') ? scenarios : scenarios.slice(0, 3);

  useEffect(() => {
    const onFrame = event => {
      const next = event.detail || {frame: null, stale: false};
      setFrameState(previous => ({
        ...next,
        receivedAt: next.frame && !next.stale ? Date.now() : previous.receivedAt
      }));
    };
    window.addEventListener('twin-dashboard-frame', onFrame);
    return () => window.removeEventListener('twin-dashboard-frame', onFrame);
  }, []);

  useEffect(() => {
    let active = true;
    let timer;
    const refreshHealth = async () => {
      try {
        const result = await requestHealth();
        if (active) setHealth(result);
      } catch {
        if (active) setHealth({status: 'UNAVAILABLE'});
      }
      if (active) timer = window.setTimeout(refreshHealth, 5000);
    };
    refreshHealth();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, []);

  const connectReported = () => {
    setError('');
    setActionMessage('');
    setSelectedDomain('');
    setSelectedDevice('');
    window.dispatchEvent(new CustomEvent('twin-dashboard-connect', {detail: {mode: 'report'}}));
  };

  const connectOperator = async event => {
    event.preventDefault();
    if (busy) return;
    const operatorPanel = event.currentTarget.closest('details');
    setBusy(true);
    setError('');
    try {
      await requestJSON('/api/session', {secret: passphrase});
      setPassphrase('');
      if (operatorPanel) operatorPanel.open = false;
      window.dispatchEvent(new CustomEvent('twin-dashboard-connect', {detail: {mode: 'operator'}}));
    } catch (failure) {
      setError(`Operator sign-in failed: ${failure.message}`);
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (busy) return;
    setBusy(true);
    setError('');
    setFrameState({frame: null, stale: false, receivedAt: null});
    window.dispatchEvent(new Event('twin-dashboard-disconnect'));
    try {
      await requestJSON('/api/logout', {});
    } catch (failure) {
      setError(`Local view cleared; server sign-out could not be confirmed (${failure.message}).`);
    } finally {
      setBusy(false);
    }
  };

  const startScenario = async event => {
    event.preventDefault();
    if (!isOperator || stale || activeScenario || scenarioBusy) return;
    setScenarioBusy(true);
    setActionMessage('');
    try {
      const result = await requestJSON('/api/operator/scenario', {scenario_id: selectedScenarioId});
      setActionMessage(`${result.scenario_id} started in the local Twin at simulation tick ${result.start_tick}.`);
    } catch (failure) {
      setError(`Scenario start rejected: ${failure.message}`);
    } finally {
      setScenarioBusy(false);
    }
  };

  const nav = [
    ['overview', 'Overview'], ['city', '3D city'], ['dual-state', 'Dual-state'],
    ['security', 'Guardian'], ['actions', 'Brain & actions'],
    ['events', 'Events'], ['devices', 'Devices'], ['scenarios', 'Scenarios']
  ];

  return <main className="shell dashboard-shell">
    <header className="topbar">
      <div className="brand">
        <div className="brand-icon"><Activity size={21}/></div>
        <div>
          <div className="eyebrow">SMART CITY · IEEE TSYP14</div>
          <h1>Convergence City <span>Digital Twin</span></h1>
        </div>
      </div>
      <div className="header-right">
        <span className={`live connection-${connection.toLowerCase()}`}><i/>{connection} · {isOperator ? 'OPERATOR' : frame ? 'REPORTED VIEW' : 'NO FRAME'}</span>
        <div className="sync-status">
          <span>Twin time · {frame?.timestamp || 'Not available'}</span>
          <span>Last sync · {latestSyncText(frameState.receivedAt)}</span>
        </div>
      </div>
    </header>

    <section className="access-strip">
      <div>
        <div className="eyebrow">START HERE · LOCAL CITY SIMULATION</div>
        <p>Choose a read-only view to explore, or sign in as operator to run a scenario and inspect private Twin diagnostics.</p>
        {frame && <small>Run {frame.run_id} · frame {frame.frame_seq} · revision {isOperator ? frame.revision : 'private'}</small>}
      </div>
      <div className="actions">
        <button className="primary" type="button" onClick={connectReported}><Radio size={15}/> Connect read-only view</button>
        <details className="operator-access">
          <summary className="secondary"><ShieldAlert size={15}/> Operator sign-in</summary>
          <form onSubmit={connectOperator}>
            <label htmlFor="operator-passphrase">Demo launch passphrase</label>
            <input id="operator-passphrase" type="password" minLength={16} maxLength={256} autoComplete="off" required value={passphrase} onChange={event => setPassphrase(event.target.value)} />
            <small className="access-hint">Local jury demo passphrase: <code>jury-city-dashboard-2026</code></small>
            <button type="submit" disabled={busy}>{busy ? 'Connecting…' : 'Connect operator'}</button>
          </form>
        </details>
        {frame && <button className="secondary" type="button" disabled={busy} onClick={disconnect}>Disconnect / sign out</button>}
      </div>
    </section>

    <section className="jury-guide" aria-labelledby="jury-guide-title">
      <div className="jury-guide-heading">
        <div><div className="eyebrow">THREE STEPS FOR THE DEMO</div><h2 id="jury-guide-title">Explore, run, review</h2></div>
        <a className="guide-action" href="#scenarios">Go to scenarios <span aria-hidden="true">↓</span></a>
      </div>
      <ol className="guide-steps">
        <li><span>1</span><div><b>Connect</b><p>Read-only view is for browsing. Operator sign-in enables scenario controls.</p></div></li>
        <li><span>2</span><div><b>Run a scenario</b><p>Open Scenarios, choose a listed test, then select <strong>Run scenario</strong>.</p></div></li>
        <li><span>3</span><div><b>Review the result</b><p>Use Twin Events for local simulation activity. Brain and Guardian have separate timelines.</p></div></li>
      </ol>
      <div className="partner-links" aria-label="Other layer dashboards">
        <span>Other layer views</span>
        <a href="http://localhost:8300/city" target="_blank" rel="noreferrer">Brain city</a>
        <a href="http://localhost:8300/timeline" target="_blank" rel="noreferrer">Brain timeline</a>
        <a href="http://localhost:8002/timeline" target="_blank" rel="noreferrer">Guardian timeline</a>
      </div>
      <p className="guide-note">The Twin dashboard shows the local simulated city. A scenario finishing here does not by itself mean Brain or Guardian approved an action.</p>
    </section>

    {error && <div className="error" role="alert">{error}</div>}
    {actionMessage && <div className="success-message" role="status">{actionMessage}</div>}
    {stale && <div className="error stale-notice" role="status">Twin connection is stale. Values below are the last received frame, not live data.</div>}

    <nav className="dashboard-nav" aria-label="Dashboard sections">
      {nav.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
      {frame && <span className="nav-scope">{isOperator ? 'Private operator data' : 'Allowlisted reported data'}</span>}
    </nav>

    <section id="overview" className="dashboard-section">
      <div className="section-heading">
        <div><div className="eyebrow">CITY OVERVIEW</div><h2>Operational picture</h2></div>
        <span className={`status-badge ${health?.status === 'UP' ? 'is-good' : health?.status === 'DOWN' ? 'is-bad' : ''}`}>
          <i/>{health?.status || 'UNKNOWN'} · local readiness
        </span>
      </div>
      <div className="overview-grid">
        <article className="overview-card"><span>Twin lifecycle</span><strong>{runtimeStatus || 'Not available'}</strong><small>Runtime state from the active frame</small></article>
        <article className="overview-card"><span>Sensors / actuators</span><strong>{isOperator ? `${sensorRegistry.length} / ${actuatorRows.length}` : 'Unknown'}</strong><small>{isOperator ? `${unavailableSensors} sensors unavailable · ${actuatorRows.filter(item => item.supported).length} actuators simulated` : 'Operator inventory is private'}</small></article>
        <article className="overview-card"><span>Flagged observations</span><strong>{frame ? (isOperator ? flaggedSensors : rows.filter(row => row.status !== 'OK').length) : 'Unknown'}</strong><small>{isOperator ? `${mismatches} real/reported mismatches · ${unavailableSensors} unavailable` : 'Only statuses in reported records are counted'}</small></article>
        <article className="overview-card"><span>Quarantined devices</span><strong>{isOperator ? quarantines.length : 'Not available'}</strong><small>{isOperator ? 'Twin quarantine state; no Guardian verdict inferred' : 'Private operator information'}</small></article>
        <article className="overview-card"><span>Pending scheduled actions</span><strong>{isOperator ? pendingActions : 'Not available'}</strong><small>{isOperator ? 'From the local Twin action queue' : 'Private operator information'}</small></article>
        <article className="overview-card"><span>Active scenario</span><strong>{activeScenario ? scenario.scenario_id : isOperator ? 'None' : 'Not available'}</strong><small>{activeScenario ? 'Scenario window is running' : 'Scenario status from the Twin frame'}</small></article>
      </div>
    </section>

    <section id="city" className="dashboard-section">
      <div className="section-heading">
        <div><div className="eyebrow">SPATIAL VIEW</div><h2>Explore the city model</h2></div>
        <span className="subtle-label">Three.js · existing GLB · Twin-linked placeholders</span>
      </div>
      <p className="section-intro">City divisions follow the available Twin zones: Port (mapped to River), Industrial, Downtown, Residential, and Health. The model is not georeferenced; marker positions are illustrative placeholders, not asset coordinates.</p>
      <div className="domain-selector" aria-label="Select urban domain">
        <button type="button" className={!selectedDomain ? 'selected' : ''} onClick={() => setSelectedDomain('')}>All domains</button>
        {DOMAINS.map(domain => <button key={domain.id} type="button" className={selectedDomain === domain.id ? 'selected' : ''}
        onClick={() => setSelectedDomain(selectedDomain === domain.id ? '' : domain.id)}>
        {domain.label}<small>{domain.note || 'CityCore zone'}</small>
        </button>)}
      </div>
      <div className="city-layout">
        <Viewer reading={waterSensor} valveFraction={valveFraction} sensorStatus={waterStatus}
          selectedDomain={selectedDomain} onSelectMarker={selectDevice}/>
        <aside className="city-inspector">
          <div className="panel-title"><span>SELECTED DOMAIN</span><small>{selectedDomain ? domainName(selectedDomain) : 'All domains'}</small></div>
          <>
            <div className="domain-status-list">
              {DOMAINS.map(domain => {
                const count = rows.filter(row => DOMAIN_ZONE[row.zone_id] === domain.id || row.zone_id === domain.id).length +
                  actuatorRows.filter(item => DOMAIN_ZONE[item.zone_id] === domain.id || item.core_zone_id === domain.id).length;
                return <button key={domain.id} type="button" className={selectedDomain === domain.id ? 'active-domain' : ''}
                  onClick={() => setSelectedDomain(selectedDomain === domain.id ? '' : domain.id)}>
                  <span>{domain.label}</span><b>{frame ? (isOperator ? count : '—') : '—'}</b>
                </button>;
              })}
            </div>
            <div className="marker-list">
              <div className="panel-title"><span>3D MARKERS</span><small>PLACEHOLDER COORDINATES</small></div>
              {MARKERS.map(marker => <button key={marker.id} type="button"
                onClick={() => {setSelectedDomain(marker.zone_id); selectDevice(marker.id); window.dispatchEvent(new CustomEvent('city-model-focus', {detail: marker.id}));}}>
                <i className={`marker-dot ${marker.id === 'S-WATER-01' ? `marker-${waterStatus.toLowerCase()}` : 'marker-unknown'}`}/>
                <span><b>{marker.id}</b><small>{marker.label} · {domainName(marker.zone_id)}</small></span>
              </button>)}
            </div>
          </>
        </aside>
      </div>
    </section>

    <section id="dual-state" className="dashboard-section">
      <div className="section-heading">
        <div><div className="eyebrow">DUAL-STATE GHOST TWIN</div><h2>What the Twin knows vs. what is reported</h2></div>
        <span className="status-badge is-info">S_real ≠ S_reported</span>
      </div>
      <p className="section-intro">Simple explanation: the right side is the value the simulated sensor reports. The left side is that sensor’s source value inside the Twin simulation. A test fault can make them different. This compares two simulated values; it does not mean physical city sensors or partner services are connected.</p>
      <div className="state-legend"><span><i className="legend-real"/>TWIN SOURCE VALUE · SIMULATION</span><span><i className="legend-reported"/>SENSOR REPORTED VALUE · OBSERVATION</span></div>
      {!frame ? <p className="empty-card">Connect reported view to see reported sensor values. Sign in as operator to compare them with private Twin source values.</p> : <>
        <p className="table-hint">Showing {Math.min(6, comparisonRows.length)} of {comparisonRows.length} readings. Use “Show all sensor readings” for the full list.</p>
        <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Sensor / domain</th><th>Twin source value (S_real)</th><th>Reported sensor value (S_reported)</th><th>Difference</th><th>Observation state</th><th>Guardian trust</th><th>Simulation time</th></tr></thead>
          <tbody>{isExpanded('comparison') ? comparisonRows : comparisonRows.slice(0, 6)}</tbody>
        </table></div></>}
      <ShowMore expanded={isExpanded('comparison')} total={comparisonRows.length} label="sensor readings"
        onClick={() => toggleList('comparison')}/>
      <p className="privacy-note">{isOperator ? 'Twin source values and differences are visible only in the authenticated operator view. A numeric mismatch is not a Guardian verdict.' : 'The reported view intentionally hides Twin source values and differences. Sign in as operator to compare S_real with S_reported.'}</p>
    </section>

    <section id="security" className="dashboard-section">
      <div className="section-heading">
        <div><div className="eyebrow">SECURITY CENTER</div><h2>Guardian information</h2></div>
        <a className="section-link" href="http://localhost:8002/timeline" target="_blank" rel="noreferrer">Open Guardian timeline ↗</a>
      </div>
      <div className="availability-banner"><ShieldAlert size={19}/><div>
        <b>Guardian verdicts are not included in this Twin frame.</b>
        <p>Open the Guardian timeline to review its service events. Twin quarantine is local simulated state; it is not proof of a Guardian decision.</p>
      </div></div>
      <div className="security-grid">
        <article className="info-card"><span>Twin quarantine records</span><strong>{isOperator ? quarantines.length : 'Private'}</strong><small>Quarantine is Twin state; it is not proof of a Guardian decision</small></article>
      </div>
      <div className="security-records">
        <h3>Quarantine and pending security events</h3>
        {isOperator && quarantines.length ? <ul>{quarantines.map(row => <li key={row.device_id}>
          <b>{row.device_id}</b><span>Quarantined in Twin</span><small>{row.reason || 'Reason not supplied'} · {row.timestamp || 'Time unavailable'}</small>
        </li>)}</ul> : <p>{isOperator ? 'No quarantined devices in the current Twin frame.' : 'Private operator information is hidden.'}</p>}
        {isOperator && frame?.events?.some(event => eventDetails(event).event_type === 'GUARDIAN_PENDING') &&
          <p className="warning-note">Scenario contains a pending Guardian marker. It is not a Guardian verdict or completed action.</p>}
      </div>
    </section>

    <section id="actions" className="dashboard-section">
      <div className="section-heading">
        <div><div className="eyebrow">DECISION AND ACTION CENTER</div><h2>Requests are not execution</h2></div>
        <a className="section-link" href="http://localhost:8300/timeline" target="_blank" rel="noreferrer">Open Brain timeline ↗</a>
      </div>
      <div className="availability-banner"><Activity size={19}/><div>
        <b>Brain decisions and partner command receipts are not included in this Twin frame.</b>
        <p>Open the Brain timeline to review Brain events. Local Twin effects describe simulated state changes, not physical actuator responses.</p>
      </div></div>
      <div className="action-columns">
        <article className="info-card"><span>Detected situation</span><strong>{scenario ? scenario.name : 'No scenario in frame'}</strong><small>Sensor status and local scenario events only; no external diagnosis inferred.</small></article>
        <article className="info-card"><span>Current simulated valve state</span><strong>{isOperator && Number.isFinite(valveFraction) ? `${Math.round(valveFraction * 100)}% open` : 'Not available'}</strong><small>Effective CityCore simulation state; not a physical acknowledgement</small></article>
      </div>
      <div className="action-history">
        <h3>Recent Twin action events</h3>
        {actionEvents.length ? <ol>{(isExpanded('actions') ? actionEvents : actionEvents.slice(0, 6)).map((event, index) => {
          const details = eventDetails(event);
          return <li key={event.event_id || event.seq || index}>
            <span className="event-kind">{eventLabel(event)}</span>
            <b>{details.device_id || details.action_id || 'Twin operation'}</b>
            <span>{Number.isFinite(details.requested) ? `Requested ${details.requested}` : details.command || 'No request value recorded'}</span>
            <span>{Number.isFinite(details.effective) ? `Effective ${details.effective}` : details.status || 'Execution detail unavailable'}</span>
            <small>{event.timestamp || `Simulation tick ${event.sim_tick ?? '—'}`}</small>
          </li>;
        })}</ol> : <p className="empty-card">{isOperator ? 'No actuator command/execution events are present in the current frame.' : 'Action history is private.'}</p>}
        <ShowMore expanded={isExpanded('actions')} total={actionEvents.length} label="action events"
          onClick={() => toggleList('actions')}/>
      </div>
    </section>

    <section id="events" className="dashboard-section">
      <div className="section-heading">
        <div><div className="eyebrow">EVENT TRACE</div><h2>Timeline</h2></div>
        <span className="subtle-label">{isOperator ? 'Private Twin events' : frame ? 'Reported observations only' : 'No frame'}</span>
      </div>
      <div className="filter-bar">
        <label>Event category<select value={eventType} onChange={event => setEventType(event.target.value)}>
          <option value="ALL">All available types</option>{eventTypes.map(type => <option key={type} value={type}>{type}</option>)}
        </select></label>
        <label>Domain<select value={eventDomainFilter} onChange={event => setEventDomainFilter(event.target.value)}>
          <option value="ALL">All domains</option>{DOMAINS.filter(domain => !domain.unavailable).map(domain =>
            <option key={domain.id} value={domain.id}>{domain.label}</option>)}
        </select></label>
        <span>“Event category” means the recorded event label (for example, ACTION_REQUESTED); it is not a city division. Severity is not supplied by the current event schema.</span>
      </div>
      {currentEvents.length ? <ol className="timeline-list">{(isExpanded('events') ? eventRows : eventRows.slice(0, 6)).map((event, index) => <li key={event.event_id || event.seq || `${eventLabel(event)}-${index}`}>
        <div className="timeline-marker"/><div><b>{eventLabel(event)}</b><p>{eventDescription(event)}</p><small>{eventDomain(event)} · {event.timestamp || `Simulation tick ${event.sim_tick ?? '—'}`}</small></div>
      </li>)}</ol> : <p className="empty-card">{frame ? 'No events match the selected filters.' : 'Connect to view available events.'}</p>}
      <ShowMore expanded={isExpanded('events')} total={eventRows.length} label="events"
        onClick={() => toggleList('events')}/>
    </section>

    <section id="devices" className="dashboard-section">
      <div className="section-heading">
        <div><div className="eyebrow">DEVICE INVENTORY</div><h2>Sensors and simulated actuators</h2></div>
        <span className="subtle-label">Physical connectivity is not reported</span>
      </div>
      <p className="section-intro">The table updates with the Twin frame. Sensor readings change at their configured sampling interval, not on every dashboard refresh. “Unbound,” “No sample,” and “Unsupported” are shown as-is rather than filled with guessed values. Actuator state is simulated only.</p>
      <div className="filter-bar">
        <label>Domain<select value={selectedDomain} onChange={event => setSelectedDomain(event.target.value)}>
          <option value="">All domains</option>{DOMAINS.map(domain => <option key={domain.id} value={domain.id}>{domain.label}</option>)}
        </select></label>
        <span>{isOperator ? `${visibleRows.length} sensor entries · ${visibleActuators.length} actuator entries` : 'Operator inventory is private'}</span>
      </div>
      {!frame ? <p className="empty-card">Connect to the Twin to inspect available devices.</p> : <>
        <p className="table-hint">{isExpanded('devices') ? `Showing all ${deviceTableRows.length} devices.` : `Showing ${Math.min(4, sensorTableRows.length)} sensors and ${Math.min(2, actuatorTableRows.length)} actuators.`} Scroll sideways to see every column.</p>
        <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Device</th><th>Type / domain</th><th>Simulation binding</th><th>Connectivity</th><th>Reported</th><th>Twin state</th><th>Last sample / frame</th><th>Status</th></tr></thead>
          <tbody>{isExpanded('devices') ? deviceTableRows : devicePreviewRows}</tbody>
        </table></div></>}
      <ShowMore expanded={isExpanded('devices')} total={deviceTableRows.length} label="devices"
        onClick={() => toggleList('devices')}/>
      {selectedDevice && <div className="device-detail">
        <div><h3>{selectedDevice}</h3><button className="text-button" type="button" onClick={() => setSelectedDevice('')}>Clear selection</button></div>
        {selectedSensor ? <>
          <p>{selectedSensor.type} · {domainName(selectedSensor.zone_id)} · trust status not available</p>
          <h4>Recent observations · simulation timestamp</h4>
          {selectedHistory.length ? <ul>{selectedHistory.map(row => <li key={`${row.seq}-${row.delivery_tick}`}>
            <span>t={row.sim_tick} · delivered t={row.delivery_tick}</span><b>{formatValue(row.reported_value, row.unit)}</b><small>{row.status}</small>
          </li>)}</ul> : <p>No retained observation for this sensor.</p>}
        </> : <p>Actuator history is not included in the current Twin frame. Displayed state is simulated, not a hardware acknowledgement.</p>}
      </div>}
    </section>

    <section id="scenarios" className="dashboard-section">
      <div className="section-heading">
        <div><div className="eyebrow">SCENARIO MANAGEMENT</div><h2>Run a defined Twin scenario</h2></div>
        <span className={`status-badge ${activeScenario ? 'is-warning' : ''}`}>{scenario ? `${scenario.scenario_id} · ${scenario.status}` : 'No active scenario'}</span>
      </div>
      <div className="scenario-layout">
        <div className="scenario-current">
          <h3>{scenario?.name || 'Scenario window is idle'}</h3>
          <p>{scenario ? `Started at simulation tick ${scenario.start_tick}. Completion means the local scenario window ended; it does not confirm Brain or Guardian completion.` : 'Choose a scenario defined by the existing Twin campaign. Scenario actions use the authenticated operator route.'}</p>
          {scenario?.correlation_id && <small>Correlation · {scenario.correlation_id}</small>}
        </div>
        <form className="scenario-launch" onSubmit={startScenario}>
          <label htmlFor="scenario-choice">Defined scenario</label>
          <select id="scenario-choice" value={selectedScenarioId} onChange={event => setScenarioChoice(event.target.value)} disabled={!isOperator || activeScenario || scenarioBusy || !scenarios.length}>
            {scenarios.map(item => <option key={item.scenario_id} value={item.scenario_id}>{item.scenario_id} · {item.name}</option>)}
          </select>
          <button type="submit" className="primary" disabled={!isOperator || stale || activeScenario || scenarioBusy || !scenarios.length}>
            {scenarioBusy ? 'Starting…' : activeScenario ? 'Scenario running' : 'Start scenario'}
          </button>
          {isOperator && !scenarios.length && <small>The Twin did not provide its scenario catalog.</small>}
          {!isOperator && <small>Operator sign-in is required. No scenario control is exposed in reported view.</small>}
          {stale && <small>Controls disabled while the displayed Twin frame is stale.</small>}
        </form>
      </div>
      <div className="scenario-caveat">
        <b>All scenarios in the current Twin catalog ({scenarios.length})</b>
        <p>These cards show scenario definitions available in the Twin; descriptions are not evidence of live hardware or partner-service data.</p>
        {scenarios.length ? <div className="scenario-catalog">
          {scenarioRows.map(item => <article key={item.scenario_id}>
            <div><b>{item.scenario_id}</b><span>{item.duration_ticks} tick window · {item.trigger_count} trigger{item.trigger_count === 1 ? '' : 's'}</span></div>
            <h4>{item.name}</h4>
            <p>{item.description || 'No description supplied by the scenario definition.'}</p>
          </article>)}
        </div> : <p>No scenario catalog was included in this Twin frame.</p>}
        <ShowMore expanded={isExpanded('scenarios')} total={scenarios.length} label="scenarios"
          onClick={() => toggleList('scenarios')}/>
      </div>
      <details className="scenario-notes">
        <summary>Scenario notes and SCN-06 behavior</summary>
        <div className="scenario-note-content">
        <b>Current implementation limits</b>
        <p>T5, SCN-02, and SCN-03 currently depend on proposal sensors whose source bindings are incomplete. SCN-04 to SCN-06 exercise local Twin scenario behavior; SCN-06 toggles controlled channel flags and does not probe partner network connections.</p>
        <p>Scenario reset is not exposed as a separate route. Resetting the simulation run is not presented as a scenario-only reset.</p>
        <div className="recovery-explainer">
        <div className="eyebrow">SCN-06 · WHAT HAPPENS ON COMMUNICATION FAILURE</div>
        <ol>
          <li><b>Channel goes unavailable</b><span>SCN-06 changes a local Brain/Guardian availability flag; it does not disconnect a real partner service.</span></li>
          <li><b>Fail closed</b><span>Brain commands are rejected while Brain is unavailable. Guardian isolation/restoration calls are rejected while Guardian is unavailable. Actuator authorization is denied if Guardian is unavailable.</span></li>
          <li><b>No hidden queue or automatic replay</b><span>Requests submitted during an outage are not queued for later execution. No physical action is claimed.</span></li>
          <li><b>After recovery</b><span>A new request must be sent. It is checked against the current Twin run and revision; stale requests are rejected. Idempotency prevents an already committed request from applying twice.</span></li>
        </ol>
        <p>This is the Twin’s tested local policy for controlled failure injection—not a demonstrated network failover, backup Brain/Guardian service, or external partner reconnection.</p>
        </div>
        </div>
      </details>
    </section>

    <footer><span>CONVERGENCE CITY · TWIN OPERATIONS</span><span>SIMULATED EFFECTS · PARTNER OUTPUTS NOT AVAILABLE</span></footer>
  </main>;
}

createRoot(document.getElementById('root')).render(<App/>);
