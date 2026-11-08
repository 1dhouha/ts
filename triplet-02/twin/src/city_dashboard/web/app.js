import {requestJSON} from './api.js';
import {FrameStore} from './store.js';

import {OperatorControls} from './controls.js';
import {DashboardHistory} from './history.js';
import {SensorWorkspace} from './sensors.js';
import {Timeline} from './timeline.js';
import {CityMap} from './map.js';
import {renderMetrics, renderReports, describeSelection} from './panels.js';

const store = new FrameStore();
const element = id => document.getElementById(id);
let mode = null;
let loop = 0;
let error = '';
let authIntent = 0;
let authBusy = false;

function setAuthBusy(value) {
  authBusy = value;
  element('secret').disabled = value;
  element('login').querySelector('button').disabled = value;
}

async function revokeSession() {
  // Keep auth serialized until server revocation is confirmed. A transport
  // failure clears private display but cannot be reported as completed logout.
  for (;;) {
    try {
      await requestJSON('/api/logout', {});
      break;
    } catch (failure) {
      if (failure.message === 'NOT_AUTHORIZED') break;
      element('auth-state').textContent = 'Sign-out pending: waiting for connection; operator login disabled.';
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
  element('auth-state').textContent = 'Signed out.';
}

const controls = new OperatorControls(store,()=>mode==='operator'&&!authBusy);
const history = new DashboardHistory();
const sensors = new SensorWorkspace(store, controls);
const timeline = new Timeline(history, controls);
const narrow=matchMedia('(max-width:1023px)');function drawers(){for(const node of document.querySelectorAll('.side-drawer'))node.open=!narrow.matches;}narrow.addEventListener('change',drawers);drawers();
const cityMap = new CityMap(element('city-map'), selectEntity);
function selectEntity(id) {if(id)element('asset-drawer').open=true;store.selection=id; cityMap.selectEntity(id); element('asset-list').value=id||''; describeSelection(store.frame,id,cityMap.layout);}
cityMap.ready.then(()=>{
  function inventory(){const query=element('search').value.toUpperCase();const list=element('asset-list');list.replaceChildren();for(const a of [...cityMap.layout.anchors,...cityMap.layout.devices]){const district=a.district||a.location_id; if(!`${a.id} ${district}`.toUpperCase().includes(query))continue;const option=document.createElement('option');option.value=a.id;option.textContent=`${a.id} / ${district}`;list.append(option);}list.value=store.selection||'';}
  inventory();element('search').addEventListener('input',inventory);render();
}).catch(()=>{element('city-map').textContent='Map unavailable. Reconnect or reload the page.';});
element('asset-list').addEventListener('change',e=>selectEntity(e.target.value));
element('zoom-in').addEventListener('click',()=>cityMap.zoom(.8));element('zoom-out').addEventListener('click',()=>cityMap.zoom(1.25));element('fit').addEventListener('click',()=>cityMap.fitCity());
for(const name of ['Traffic','Water','Power','Air','Emergency','Sensor']){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=true;input.setAttribute('aria-label',`${name} layer`);input.addEventListener('change',()=>cityMap.setLayer(name,input.checked));label.append(input,name);element('layers').append(label);}
document.addEventListener('keydown',e=>{if(e.key==='Escape')selectEntity(null);});
function render() {
  performance.clearMeasures('city-frame-render');performance.mark('city-render-start');
  element('connection').textContent = mode ? (store.stale ? `STALE / ${error || 'waiting'}` : 'CONNECTED') : 'DISCONNECTED';
  element('visibility').textContent = store.frame?.visibility || 'No frame loaded.';
  element('identity').textContent = store.frame ? `Run ${store.frame.run_id} · frame ${store.frame.frame_seq}` : 'Waiting for connection.';
  controls.render();sensors.render();history.accept(store.frame);timeline.render(store.frame);
  document.body.classList.toggle('stale',store.stale);
  element('clock').textContent=store.frame?.status ? `${store.frame.status.state} · t=${store.frame.sim_tick}s · ${store.frame.status.speed}×` : 'Reported observations only';
  renderMetrics(store.frame);renderReports(store.frame);cityMap.renderFrame(store.frame,{stale:store.stale});cityMap.selectEntity(store.selection);describeSelection(store.frame,store.selection,cityMap.layout);
  if(cityMap.layout){const signature=cityMap.layout.devices.map(d=>d.id).join('|');if(signature!==element('asset-list').dataset.inventory){element('asset-list').dataset.inventory=signature;element('search').dispatchEvent(new Event('input'));}}
  element('frame').textContent = store.frame ? JSON.stringify(store.frame, null, 2) : 'No data.';
  performance.measure('city-frame-render','city-render-start');performance.clearMarks('city-render-start');
}

function connect(nextMode) {
  mode = nextMode;
  store.clear();
  error = '';
  const ticket = ++loop;
  async function poll() {
    if (ticket !== loop) return;
    const generation = store.begin();
    try {
      const frame = await requestJSON(`/api/${nextMode}/frame`);
      if (ticket !== loop) return;
      if (!store.accept(frame, generation)) throw new Error('INVALID_FRAME');
      error = '';
    } catch (failure) {
      if (ticket !== loop) return;
      store.fail();
      error = failure.message;
    }
    render();
    if (ticket === loop) setTimeout(poll, 250);
  }
  render();
  poll();
}

element('login').addEventListener('submit', async event => {
  event.preventDefault();
  if (authBusy) return;
  const intent = ++authIntent;
  setAuthBusy(true);
  element('auth-state').textContent = 'Operator login pending.';
  const secret = element('secret').value;
  element('secret').value = '';
  try {
    await requestJSON('/api/session', {secret});
    if (intent === authIntent) {
      connect('operator');
      document.querySelector('.connection-panel').open=false;
      element('auth-state').textContent = 'Operator session connected.';
    } else {
      await revokeSession();
    }
  } catch (failure) {
    if (intent === authIntent) {
      element('auth-state').textContent = `LOGIN FAILED / ${failure.message}`;
    } else {
      await revokeSession();
    }
  } finally {
    setAuthBusy(false);
  }
});
element('report').addEventListener('click', () => {
  ++authIntent;
  connect('report');
});
element('logout').addEventListener('click', async () => {
  ++authIntent;
  ++loop;
  mode = null;
  store.clear();
  render();
  element('auth-state').textContent = 'Sign-out pending.';
  if (authBusy) return; // The obsolete login owns its follow-up revocation.
  setAuthBusy(true);
  try { await revokeSession(); }
  finally { setAuthBusy(false); }
});
setInterval(() => { if (mode && store.stale) render(); }, 250);
window.addEventListener('pagehide', () => { ++loop; });
