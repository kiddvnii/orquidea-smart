'use strict';
const $ = (id) => document.getElementById(id);
const DAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];
let wateringEvents = [];
let lastHistoryCount = -1;
let lastWarningSignature = '';
const localDate = (value) => new Date(value).toLocaleString('es-CL', { dateStyle:'short', timeStyle:'short' });
const fmt = (num) => Number(num).toFixed(1);

function toast(message, isError = false) {
  const node = document.createElement('div');
  node.className = `toast${isError ? ' error' : ''}`;
  node.textContent = message;
  $('toastRegion').appendChild(node);
  setTimeout(() => node.remove(), 5200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {cache:'no-store', credentials:'same-origin', ...options});
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    if (response.status === 401) { toast('Tu sesión expiró. Vuelve a ingresar.', true); setTimeout(() => location.assign('/login'), 900); }
    throw new Error(data.detail || `Error ${response.status}`);
  }
  return data;
}
const jsonOpts = (method, body) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

function updateMetrics(reading) {
  const pill = $('connectionPill');
  const humidity = reading?.humidity;
  const temp = reading?.temperature;
  if (!reading) {
    pill.className = 'connection-pill'; pill.innerHTML = '<span class="status-dot"></span> Sin datos';
    $('environmentStatus').textContent = 'Sin información';
    $('environmentDetail').textContent = 'Envía una lectura desde el simulador para comenzar.';
    $('alertBanner').hidden = true;
    return;
  }
  $('humidityValue').textContent = fmt(humidity);
  $('temperatureValue').textContent = fmt(temp);
  $('humidityBar').style.width = `${Math.min(100, humidity)}%`;
  $('temperatureBar').style.width = `${Math.max(0,Math.min(100,(temp + 20) / 80 * 100))}%`;
  $('lastUpdated').textContent = localDate(reading.created_at);
  const isStale = (Date.now() - new Date(reading.created_at).getTime()) > 90_000;
  pill.className = `connection-pill ${isStale ? 'stale' : 'online'}`;
  pill.innerHTML = `<span class="status-dot"></span> ${isStale ? 'Lectura antigua' : 'Recibiendo datos'}`;
  const warnings = [];
  if (humidity < 75) warnings.push('La humedad está por debajo del 75 %.');
  if (humidity > 85) warnings.push('La humedad supera el 85 %.');
  if (temp < 18) warnings.push('La temperatura está por debajo de 18 °C.');
  if (temp > 24) warnings.push('La temperatura supera los 24 °C.');
  $('humidityState').textContent = humidity >= 75 && humidity <= 85 ? '✓ Humedad dentro del rango esperado' : '⚠ Revisar nivel de humedad';
  $('temperatureState').textContent = temp >= 18 && temp <= 24 ? '✓ Temperatura adecuada' : '⚠ Revisar temperatura';
  if (warnings.length) {
    $('environmentStatus').textContent = 'Requiere atención';
    $('environmentDetail').textContent = 'Una o más condiciones están fuera del rango.';
    $('alertBanner').hidden = false;
    $('alertTitle').textContent = 'Alerta ambiental';
    $('alertDescription').textContent = warnings.join(' ');
    const signature = warnings.join('|');
    // Solo una alerta emergente nueva cuando cambian las condiciones y la lectura es reciente.
    if (!isStale && signature !== lastWarningSignature) toast(warnings.join(' '), true);
    lastWarningSignature = signature;
  } else {
    $('environmentStatus').textContent = 'Ambiente óptimo';
    $('environmentDetail').textContent = 'Las mediciones cumplen los valores establecidos.';
    $('alertBanner').hidden = true;
    lastWarningSignature = '';
  }
}

function makeChart(id, readings, field, color, target) {
  const host = $(id);
  if (!readings.length) { host.innerHTML = '<p class="chart-empty">Aún no hay mediciones registradas</p>'; return; }
  const W = 600, H = 180, L = 38, R = 15, T = 12, B = 26;
  const series = readings.map(r => +r[field]);
  const anchor = Array.isArray(target) ? target : [target];
  const values = series.concat(anchor);
  const min = Math.min(...values), max = Math.max(...values);
  const padding = Math.max(field === 'humidity' ? 3 : 1.5, (max - min) * .22);
  const yMin = field === 'humidity' ? Math.max(0, min-padding) : min-padding;
  const yMax = field === 'humidity' ? Math.min(100, max+padding) : max+padding;
  const y = val => +(T+(yMax-val)/(yMax-yMin)*(H-T-B)).toFixed(2);
  const x = index => +(L+(series.length === 1 ? (W-R-L)/2 : (index/(series.length-1))*(W-R-L))).toFixed(2);
  const points = series.map((v,i)=> `${x(i)},${y(v)}`).join(' ');
  const lines = [0,.25,.5,.75,1].map(f => {
    const value = yMax-f*(yMax-yMin), pos=y(value);
    return `<line x1="${L}" x2="${W-R}" y1="${pos}" y2="${pos}" stroke="#e9efea" stroke-dasharray="4 4"/><text x="${L-7}" y="${pos+3}" text-anchor="end" fill="#9aac9c" font-size="10">${value.toFixed(0)}</text>`;
  }).join('');
  const band = Array.isArray(target) ? `<rect x="${L}" y="${y(target[1])}" width="${W-L-R}" height="${y(target[0])-y(target[1])}" fill="${color}" opacity=".07"/>` : `<line x1="${L}" x2="${W-R}" y1="${y(target)}" y2="${y(target)}" stroke="${color}" stroke-dasharray="6 5" opacity=".55"/>`;
  const circle = `<circle cx="${x(series.length-1)}" cy="${y(series[series.length-1])}" r="4.5" fill="${color}" stroke="white" stroke-width="2"/>`;
  const firstTime = new Date(readings[0].created_at).toLocaleTimeString('es-CL',{hour:'2-digit',minute:'2-digit'});
  const lastTime = new Date(readings[readings.length-1].created_at).toLocaleTimeString('es-CL',{hour:'2-digit',minute:'2-digit'});
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Gráfico de ${field==='humidity'?'humedad':'temperatura'} con ${series.length} mediciones" preserveAspectRatio="none">${band}${lines}<polyline points="${points}" stroke="${color}" stroke-width="2.8" fill="none" stroke-linejoin="round" stroke-linecap="round"/>${circle}<text x="${L}" y="${H-5}" fill="#92a69a" font-size="10">${firstTime}</text><text x="${W-R}" text-anchor="end" y="${H-5}" fill="#92a69a" font-size="10">${lastTime}</text></svg>`;
}

async function refreshHistory() {
  try {
    const data = await api('/api/history?limit=100');
    makeChart('humidityChart', data.items, 'humidity', '#3b9970', 80);
    makeChart('temperatureChart', data.items, 'temperature', '#d68c66', [18,24]);
  } catch(e) { console.warn('No se pudo cargar historial:', e); }
}
function renderWatering(events) {
  const host = $('wateringList');
  $('wateringCount').textContent = `${events.length} ${events.length===1?'tarea':'tareas'}`;
  host.replaceChildren();
  if (!events.length) { const empty=document.createElement('div'); empty.className='empty-list'; empty.textContent='No hay riegos programados.'; host.appendChild(empty); return; }
  for (const event of events) {
    const item = document.createElement('div'); item.className = 'watering-item';
    const icon = document.createElement('span'); icon.className='item-icon'; icon.textContent='♧';
    const info = document.createElement('div'); info.className='watering-info';
    const name = document.createElement('strong'); name.textContent=event.name;
    const subtitle = document.createElement('small'); subtitle.textContent=`${event.weekdays.map(d=>DAYS[d]).join(', ')} · ${event.time}${event.last_watered?' · Regado '+localDate(event.last_watered):''}`;
    info.append(name,subtitle);
    const actions = document.createElement('div'); actions.className='item-actions';
    const done = document.createElement('button'); done.textContent='✓ Regado'; done.title='Registrar riego realizado';
    done.onclick = async() => {try{await api(`/api/watering/${event.id}/complete`,{method:'POST'});toast('Riego registrado correctamente.');refresh();}catch(e){toast(e.message,true);}};
    const del = document.createElement('button'); del.className='delete-btn'; del.textContent='Eliminar';
    del.onclick=async()=>{if(!confirm(`¿Eliminar riego «${event.name}»?`))return;try{await api(`/api/watering/${event.id}`,{method:'DELETE'});toast('Riego eliminado.');refresh();}catch(e){toast(e.message,true);}};
    actions.append(done,del);item.append(icon,info,actions);host.appendChild(item);
  }
}

async function refresh() {
  try {
    const data=await api('/api/overview');
    updateMetrics(data.latest);
    $('historyCount').textContent = data.history_count;
    $('historyInterval').value = String(data.history_interval);
    wateringEvents = data.watering;
    renderWatering(wateringEvents);
    if(lastHistoryCount!==data.history_count){lastHistoryCount=data.history_count;await refreshHistory();}
    checkWateringDue();
  } catch(e) { $('connectionPill').className='connection-pill stale';$('connectionPill').textContent='Sin conexión con servidor';console.warn(e); }
}

function notify(message) {
  toast(message);
  if ('Notification' in window && Notification.permission === 'granted') {
    new Notification('Orquídea Smart · Hora de riego', {body:message,icon:'/static/flower.svg'});
  }
}
function checkWateringDue() {
  const now=new Date(), day=(now.getDay()+6)%7;
  const currentMinutes=now.getHours()*60+now.getMinutes();
  const stamp=`${now.getFullYear()}-${now.getMonth()+1}-${now.getDate()}`;
  for(const event of wateringEvents){
    if(!event.enabled||!event.weekdays.includes(day))continue;
    const [h,m]=event.time.split(':').map(Number), due=h*60+m;
    // Ventana de 5 minutos para evitar perder avisos por intervalos de consulta.
    if(currentMinutes<due||currentMinutes>due+4)continue;
    const key=`orchid-notified-${event.id}-${stamp}`;
    if(localStorage.getItem(key))continue;
    localStorage.setItem(key,'1');
    notify(`Es momento de regar: ${event.name} (${event.time}).`);
  }
}

$('wateringForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const weekdays=[...document.querySelectorAll('#weekdayGroup input:checked')].map(c=>+c.value);
  if(!weekdays.length){toast('Selecciona al menos un día de riego.',true);return;}
  try{
    await api('/api/watering',jsonOpts('POST',{name:$('wateringName').value.trim(),weekdays,time:$('wateringTime').value}));
    $('wateringName').value='';document.querySelectorAll('#weekdayGroup input').forEach(c=>c.checked=false);
    toast('Riego programado correctamente.');refresh();
  }catch(err){toast(err.message,true);}
});
$('saveSettings').addEventListener('click',async()=>{
  try{await api('/api/settings',jsonOpts('PUT',{history_interval:+$('historyInterval').value}));toast('Frecuencia del historial guardada.');refresh();}catch(e){toast(e.message,true);}
});
$('notifyPermission').addEventListener('click',async()=>{
  if(!('Notification' in window)){toast('Este navegador no admite notificaciones.',true);return;}
  if(!window.isSecureContext){toast('Las notificaciones del navegador requieren HTTPS (o localhost).',true);return;}
  const status=await Notification.requestPermission();toast(status==='granted'?'Notificaciones habilitadas.':'No se habilitaron las notificaciones.',status!=='granted');
});
$('testNotification').addEventListener('click',()=>notify('Recordatorio de prueba: revisa el riego de tus orquídeas.'));
$('logoutBtn').addEventListener('click',async()=>{await api('/api/logout',{method:'POST'});location.href='/login';});
function clock(){ $('clock').textContent=new Date().toLocaleTimeString('es-CL',{hour:'2-digit',minute:'2-digit'}); }
clock();refresh();setInterval(clock,30_000);setInterval(refresh,8000);setInterval(checkWateringDue,20_000);
