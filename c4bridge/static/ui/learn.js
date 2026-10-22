/* Anlern-Assistent (experimentell) – Admin › Einrichtung › Geräte.
   Liest die Control4-Daten eines Treibers (Familie = Treiber + Proxys), schneidet nur dessen Push mit, während man das
   Gerät in der Control4-App bedient, und leitet daraus ein Profil ab (Daten, kein Code) → data/learned.json.
   Backend: c4bridge/learn.py. Nutzt die Helfer der Admin-Seite ($, api, post, esc). */
'use strict';
const L = (() => {
  // Rolle des Items, das den Befehl wirklich ausführt (manche Proxys listen Befehle des Treibers)
  const tgt = c => { const m = ((st.sug && st.sug.members) || []).find(x => x.id === c.target); return m && m.role !== c.role ? { target_role: m.role } : {}; };
  const fresh = () => ({ id: null, d: null, tab: 'map', view: 'need', sug: null, type: '', sel: { fields: {}, actions: {}, infos: [] },
    devRole: '', ref: false, ev: null, cap: null, msg: '' });
  let st = fresh(), schemas = null, capTimer = null, pickQ = '';
  const box = () => $('#learnbox');
  const del = path => api(path, { method: 'DELETE' });
  const sv = x => (x == null ? '' : typeof x === 'object' ? JSON.stringify(x) : String(x));
  const short = (x, n = 70) => { const s = sv(x); return s.length > n ? s.slice(0, n) + '…' : s; };
  const cid = c => `${c.src}|${c.role}|${c.key}`;
  const SRC = { var: 'Variable', prop: 'Eigenschaft', event: 'Push' };
  const ROLE = r => (r === 'driver' ? 'Treiber' : r.replace(/^proxy:/, ''));

  async function open(id) {
    if (!$('#learnbox')) {                       // aus einer anderen Ansicht: Einrichtung › Geräte öffnen
      show('config'); cfgSub = 'devices';
      for (let i = 0; i < 30 && !$('#learnbox'); i++) await new Promise(r => setTimeout(r, 100));
      if (!$('#learnbox')) { cfgSub = 'devices'; renderConfigTab(); }
    }
    stopPoll();
    st = fresh();
    if (!schemas) schemas = await api('/api/learn/schemas').catch(() => ({}));
    box().scrollIntoView({ block: 'start' });
    if (id == null) return renderPick();
    await load(id);
  }
  function close() { stopPoll(); st = fresh(); const b = box(); if (b) b.innerHTML = ''; }

  // ---------------------------------------------------------------- 1. Gerät wählen
  async function renderPick() {
    const prof = await api('/api/learn/profiles').catch(() => ({ profiles: {}, items: {} }));
    const items = (typeof allItems !== 'undefined' ? allItems : []);
    const q = pickQ.toLowerCase();
    const order = { learned: 0, candidate: 1, feeds: 2, media: 3, device: 4, module: 5, infra: 6, agent: 7 };
    const hits = items.filter(i => !q || `${i.name} ${i.id} ${i.proxy} ${i.driver} ${i.room}`.toLowerCase().includes(q))
      .sort((a, b) => (order[a.use] ?? 9) - (order[b.use] ?? 9) || a.name.localeCompare(b.name)).slice(0, 60);
    box().innerHTML = `<div class="panel learn">
      <div class="toolbar"><h2 class="sec" style="margin:0">🧪 Gerät anlernen <span class="tag warn">experimentell</span></h2>
        <button class="act spacer" onclick="L.close()">schließen</button></div>
      <p class="hint" style="margin-top:0">So geht's: <b>1.</b> Gerät wählen · <b>2.</b> „Mitschnitt“ starten und das Gerät in der
        Control4-App bedienen · <b>3.</b> „Zuordnen“: der Assistent schlägt aus Variablen, Eigenschaften, Befehlen und dem Mitschnitt
        vor, was die Bridge braucht – du bestätigst oder wählst 1:1 aus · <b>4.</b> Profil speichern und prüfen.
        Lesen ist gefahrlos; nur „Testen“ schickt einen Befehl (mit Rückfrage).</p>
      <div class="toolbar">
        <input type="search" placeholder="Gerät, Proxy, Treiber, Raum suchen …" value="${esc(pickQ)}" oninput="L.pickSearch(this.value)" id="lq">
        <input type="number" id="lid" placeholder="Control4-ID" style="width:130px" onkeydown="if(event.key==='Enter')L.open(+this.value)">
        <button class="act" onclick="L.open(+$('#lid').value)">ID laden</button>
        <button class="act" onclick="L.rescan(this)">Gerät nicht dabei? Projekt neu einlesen</button>
      </div>
      <div class="scroll"><table><tr><th>Item (ID)</th><th>Raum</th><th>Proxy / Treiber</th><th>Nutzung durch die Bridge</th><th></th></tr>
        ${hits.map(i => `<tr><td>${esc(i.name)}<div class="hint">#${i.id}</div></td><td>${esc(i.room || '')}</td>
          <td><code>${esc(i.proxy)}</code><div class="hint">${esc(i.driver)}</div></td><td class="small">${esc(i.label)}</td>
          <td><button class="act" onclick="L.open(${i.id})">wählen</button></td></tr>`).join('')}</table></div>
      ${Object.keys(prof.profiles || {}).length ? `<div class="sect"><h2 class="sec">Gespeicherte Profile</h2><table>
        <tr><th>Schlüssel</th><th>Typ</th><th>Felder / Aktionen</th><th>angelernt an</th><th></th></tr>
        ${Object.entries(prof.profiles).map(([k, p]) => `<tr><td><code>${esc(k)}</code></td><td>${esc(p.type)}${p.reference ? ' <span class="tag">Referenz</span>' : ''}</td>
          <td class="small">${esc(Object.keys(p.fields || {}).join(', '))}<br>${esc(Object.keys(p.actions || {}).join(', '))}</td>
          <td><a href="#" onclick="event.preventDefault();L.open(${p.learned_from})">#${p.learned_from}</a></td>
          <td><button class="act" onclick="L.delProfile('${esc(k)}')">löschen</button></td></tr>`).join('')}</table></div>` : ''}
    </div>`;
    const lq = $('#lq'); if (lq && pickQ) { lq.focus(); lq.setSelectionRange(pickQ.length, pickQ.length); }
  }
  let pickT;
  function pickSearch(v) { pickQ = v; clearTimeout(pickT); pickT = setTimeout(renderPick, 200); }
  async function rescan(btn) {
    btn.disabled = true; btn.textContent = 'lese ein …';
    try { await post('/api/scan'); if (typeof loadDevices === 'function') await loadDevices(); } catch (e) { alert(e.message); }
    renderPick();
  }
  async function delProfile(k) {
    if (!confirm(`Profil ${k} löschen?`)) return;
    await del('/api/learn/profile?key=' + encodeURIComponent(k)); renderPick();
  }

  // ---------------------------------------------------------------- 2. Untersuchen
  async function load(id) {
    if (!id) return;
    box().innerHTML = `<div class="panel"><span class="hint">lese #${id} vom Director …</span></div>`;
    try {
      st.d = await api(`/api/learn/inspect/${id}`);
    } catch (e) { box().innerHTML = `<div class="panel"><p style="color:var(--err)">${esc(e.message)}</p><button class="act" onclick="L.open()">zurück</button></div>`; return; }
    st.id = id;
    const p = st.d.profile;
    st.type = (p && p.type) || st.d.suggest.type;
    if (p) { st.sel = { fields: { ...(p.fields || {}) }, actions: { ...(p.actions || {}) }, infos: (p.infos || []).map(x => ({ ...x })) };
      st.devRole = p.device_role || ''; st.ref = !!p.reference; }
    if (!p && ((st.d.feeds || []).length || (st.d.used || []).length)) st.ref = true;   // Dublettenschutz
    await suggest(!p);
    if (!st.devRole) st.devRole = st.sug.role || 'driver';
    try { st.cap = await api(`/api/learn/capture/${id}`); if (st.cap.root !== st.d.root) st.cap = null; } catch { st.cap = null; }
    if (st.cap && st.cap.active) poll();
    render();
  }
  async function suggest(autofill) {
    st.sug = await api(`/api/learn/suggest/${st.id}?type=${st.type}`);
    const sch = schemas[st.type] || { fields: {}, actions: {} };
    if (autofill) {                                // Vorschlag übernehmen: bester Kandidat je Feld/Aktion
      st.sel = { fields: {}, actions: {}, infos: st.sel.infos || [] };
      for (const f of Object.keys(sch.fields)) {
        const c = (st.sug.fields[f] || [])[0];
        if (c) st.sel.fields[f] = { src: c.src, role: c.role, key: c.key, ...(c.map ? { map: c.map } : {}) };
      }
      for (const a of Object.keys(sch.actions)) {
        const c = (st.sug.actions[a] || [])[0];
        if (c) st.sel.actions[a] = { role: c.role, command: c.command, ...tgt(c), ...(sch.actions[a].param ? { param: (c.params[0] || {}).name || '' } : {}) };
      }
    }
  }
  function render() {
    const d = st.d; if (!d || !box()) return;
    const dev = (typeof devs !== 'undefined' ? devs : []).find(x => x.id === st.id);
    const tabs = [['map', 'Zuordnen'], ['cap', `Mitschnitt${st.cap && st.cap.count ? ' (' + st.cap.count + ')' : ''}`], ['vars', 'Variablen'],
      ['props', `Eigenschaften (${d.properties.length})`], ['cmds', 'Befehle'], ['fam', `Familie (${d.members.length}) & Bindungen`]];
    box().innerHTML = `<div class="panel learn">
      <div class="toolbar"><h2 class="sec" style="margin:0">🧪 ${esc(d.name)} <span class="hint">#${st.id}</span></h2>
        <span class="tag">${esc(d.proxy || '')}</span><span class="hint">${esc(d.driver || '')} · ${esc(d.room || '')} · Treiber #${d.root}</span>
        ${dev && typeof actionCell === 'function' && actionCell(dev) ? `<span class="hint">Test:</span> ${actionCell(dev)}` : ''}
        <button class="act spacer" onclick="L.open()">anderes Gerät</button><button class="act" onclick="L.close()">schließen</button></div>
      ${(d.feeds || []).length ? `<p class="note" style="color:var(--warn);margin:0 0 12px">⚠ Dieser Treiber speist bereits ${d.feeds.map(f =>
        `<b>${esc(f.name)}</b> #${f.id} (${esc(f.type)}, Bindung „${esc(f.binding)}“)`).join(', ')} – das wird schon angezeigt.
        Ein Profil wird deshalb nicht als Gerät übernommen (Dublettenschutz) – „nur Referenz“ ist vorgewählt.</p>` : ''}
      ${(d.used || []).length ? `<p class="note" style="color:var(--warn);margin:0 0 12px">⚠ Die Bridge nutzt diesen Treiber schon: ${d.used.map(u =>
        `<b>${esc(u.name)}</b> #${u.id} (${esc(u.label)})`).join(', ')}. Rangfolge Proxy → Modul → Profil: ein Profil würde nicht
        übernommen – speichern nur zum Nachschlagen („nur Referenz“).</p>` : ''}
      <div class="seg" style="margin-bottom:14px">${tabs.map(([k, l]) => `<button type="button" class="${st.tab === k ? 'on' : ''}" onclick="L.tab('${k}')">${l}</button>`).join('')}</div>
      <div id="lbody">${body()}</div></div>`;
  }
  function tab(k) { st.tab = k; render(); if (k === 'cap' && st.cap && st.cap.active) poll(); }
  function body() {
    const d = st.d;
    if (st.tab === 'vars') return d.members.map(m => `<h3 class="lsub">${esc(m.name)} <span class="hint">#${m.id} · ${esc(ROLE(m.role))}</span></h3>
      ${m.variables.length ? `<table>${m.variables.map(x => `<tr><th style="width:40%">${esc(x.name)}</th><td>${esc(short(x.value, 160))}</td></tr>`).join('')}</table>` : '<p class="hint">keine Variablen</p>'}`).join('');
    if (st.tab === 'props') return d.properties.length ? `<table>${d.properties.map(x => `<tr><th style="width:40%">${esc(x.name)}</th>
      <td>${esc(short(x.value, 160))}</td><td class="hint">${esc(x.type || '')}${x.readonly ? ' · nur lesen' : ''}</td></tr>`).join('')}</table>` : '<p class="hint">keine Eigenschaften</p>';
    if (st.tab === 'cmds') return d.members.filter(m => m.commands.length).map(m => `<h3 class="lsub">${esc(m.name)} <span class="hint">#${m.id} · ${esc(ROLE(m.role))}</span></h3>
      <table>${m.commands.map(c => `<tr><th style="width:40%">${esc(c.command)}</th><td>${esc(c.label !== c.command ? c.label : '')}</td>
      <td class="small">${c.params.map(p => esc(`${p.name} (${p.type || '?'}${p.minimum != null ? ' ' + p.minimum + '–' + p.maximum : ''})`)).join(', ')}</td><td class="hint">→ #${c.target}</td></tr>`).join('')}</table>`).join('') || '<p class="hint">keine Befehle</p>';
    if (st.tab === 'fam') return `<table><tr><th>Item</th><th>Proxy</th><th>Rolle</th><th>Treiber</th></tr>${d.members.map(m => `<tr><td>${esc(m.name)} <span class="hint">#${m.id}</span></td>
      <td><code>${esc(m.proxy)}</code></td><td>${esc(ROLE(m.role))}</td><td class="hint">${esc(m.driver)}</td></tr>`).join('')}</table>
      <h3 class="lsub">Bindungen des Treibers</h3>${d.bindings.length ? `<table>${d.bindings.map(b => `<tr><th style="width:30%">${esc(b.name)} <span class="hint">${b.id}</span></th>
      <td class="small">${esc(b.class || '')} · ${esc(b.type || '')}</td><td>${b.to.map(t => `${esc(t.name)} <span class="hint">#${t.id}${t.room ? ' · ' + esc(t.room) : ''}</span>`).join('<br>')}</td></tr>`).join('')}</table>` : '<p class="hint">keine verbundenen Bindungen</p>'}`;
    if (st.tab === 'cap') return capHtml();
    return mapHtml();
  }

  // ---------------------------------------------------------------- Mitschnitt
  function capHtml() {
    const c = st.cap;
    const rows = c ? c.messages.slice().reverse() : [];
    return `<div class="toolbar">
        <select id="lsec">${[60, 120, 300, 600].map(s => `<option value="${s}" ${s === 120 ? 'selected' : ''}>${s / 60} min</option>`).join('')}</select>
        <button class="act primary" onclick="L.capStart()">${c && c.active ? 'neu starten' : 'Mitschnitt starten'}</button>
        ${c && c.active ? `<button class="act" onclick="L.capStop()">stoppen</button>` : ''}
        <span class="hint">${c ? (c.active ? `läuft noch ${c.left} s · ` : 'beendet · ') + `${c.count} Nachrichten` : 'noch kein Mitschnitt'}</span></div>
      <p class="hint" style="margin-top:0">Nur Nachrichten dieses Treibers und seiner Proxys. ${c && c.active ? '<b>Jetzt das Gerät in der Control4-App bedienen</b> (an/aus, Titel wechseln, Lautstärke …).' : ''}
        Die gesehenen Felder erscheinen danach unter „Zuordnen“ als Quelle „Push“.</p>
      ${rows.length ? `<div class="scroll" style="max-height:420px;overflow-y:auto"><table><tr><th>Zeit</th><th>Von</th><th>Felder</th></tr>
        ${rows.map(m => `<tr><td class="hint">${new Date(m.t * 1000).toLocaleTimeString('de-DE')}</td><td class="small">${esc(m.who)}</td>
          <td class="small">${m.flat.map(([k, v]) => `<code>${esc(k)}</code> = ${esc(short(v, 90))}`).join('<br>')}</td></tr>`).join('')}</table></div>` : ''}`;
  }
  async function capStart() {
    st.cap = await post(`/api/learn/capture/${st.id}`, { seconds: +($('#lsec') || {}).value || 120 });
    render(); poll();
  }
  async function capStop() { await del('/api/learn/capture'); stopPoll(); st.cap = await api(`/api/learn/capture/${st.id}`); render(); }
  function stopPoll() { clearInterval(capTimer); capTimer = null; }
  function poll() {
    stopPoll();
    capTimer = setInterval(async () => {
      if (!box() || !st.id) return stopPoll();
      try { st.cap = await api(`/api/learn/capture/${st.id}`); } catch { return; }
      if (st.tab === 'cap') $('#lbody').innerHTML = capHtml(); else render();
      if (!st.cap.active) { stopPoll(); await suggest(false); render(); }
    }, 2000);
  }

  // ---------------------------------------------------------------- 3. Zuordnen + 4. Speichern/Prüfen
  function mapHtml() {
    const sch = schemas[st.type] || { label: st.type, fields: {}, actions: {} }, s = st.sug || {};
    const all = s.all || [];
    const optC = (f, cur) => {
      const ranked = (s.fields[f] || []).map(cid), seen = new Set();
      const o = c => { const k = cid(c); if (seen.has(k)) return ''; seen.add(k);
        return `<option value="${esc(k)}" ${cur && cid(cur) === k ? 'selected' : ''}>${ranked.includes(k) ? '★ ' : ''}${SRC[c.src]} ${esc(c.key)} @ ${esc(ROLE(c.role))} = ${esc(short(c.value, 30))}</option>`; };
      return `<option value="">– nicht belegt –</option>` + (s.fields[f] || []).map(o).join('')
        + `<optgroup label="alle Quellen">${all.map(o).join('')}</optgroup>`;
    };
    const fieldRow = (f, spec) => {
      const cur = st.sel.fields[f];
      const curC = cur && all.find(c => cid(c) === cid(cur));
      const value = curC ? curC.value : cur ? '(nicht im aktuellen Stand)' : '';
      const sugC = cur && (s.fields[f] || []).find(c => cid(c) === cid(cur));
      const vals = spec.enum && cur ? Object.keys({ ...(sugC && sugC.map) || {}, ...(cur.map || {}), ...(curC ? { [sv(curC.value)]: '' } : {}) }) : [];
      return `<tr><th style="width:22%">${esc(spec.label)}${spec.req ? ' <span style="color:var(--warn)">*</span>' : ''}<div class="hint">${f}</div></th>
        <td><select style="max-width:520px;width:100%" onchange="L.setField('${f}', this.value)">${optC(f, cur)}</select>
          ${spec.enum && cur ? `<div class="lmap">${vals.filter(x => x !== '').map(x => {
            const sel = (cur.map || {})[x] || (sugC && sugC.map || {})[x] || '';
            const opts = [...new Set([...spec.enum, ...Object.values(cur.map || {}).filter(Boolean)])];
            return `<span><code>${esc(short(x, 24))}</code> →
            <select onchange="L.setMap('${f}', ${esc(JSON.stringify(x))}, this.value, this)"><option value="">?</option>${opts.map(e =>
              `<option ${sel === e ? 'selected' : ''}>${esc(e)}</option>`).join('')}<option value="__own">eigener Zustand …</option></select></span>`; }).join('')}</div>` : ''}</td>
        <td class="small" style="width:22%">${esc(short(value, 60))}</td></tr>`;
    };
    const cmds = s.commands || [];
    const actRow = (a, spec) => {
      const cur = st.sel.actions[a], ranked = (s.actions[a] || []).map(c => c.role + '|' + c.command);
      const cmd = cur && cmds.find(c => c.role === cur.role && c.command === cur.command);
      return `<tr><th style="width:22%">${esc(spec.label)}<div class="hint">${a}</div></th>
        <td><select style="max-width:520px;width:100%" onchange="L.setAction('${a}', this.value)"><option value="">– nicht belegt –</option>
          ${cmds.slice().sort((x, y) => ranked.includes(y.role + '|' + y.command) - ranked.includes(x.role + '|' + x.command)).map(c => {
            const k = c.role + '|' + c.command;
            return `<option value="${esc(k)}" ${cur && cur.role + '|' + cur.command === k ? 'selected' : ''}>${ranked.includes(k) ? '★ ' : ''}${esc(c.command)} @ ${esc(ROLE(c.role))}</option>`; }).join('')}</select></td>
        <td class="small" style="width:22%">${spec.param ? `Wert-Parameter: ${cmd && cmd.params.length ? `<select onchange="L.setParam('${a}', this.value)">${cmd.params.map(p =>
          `<option ${cur.param === p.name ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>` : `<input style="width:120px" value="${esc((cur && cur.param) || '')}" placeholder="z. B. LEVEL" onchange="L.setParam('${a}', this.value)">`}` : ''}</td></tr>`;
    };
    const sc = s.scores || {};
    const missing = Object.entries(sch.fields).filter(([f, x]) => x.req && !st.sel.fields[f]).map(([, x]) => x.label);
    const nChanged = all.filter(c => c.changed).length;
    const views = `<div class="seg" style="margin-bottom:12px"><button type="button" class="${st.view === 'need' ? 'on' : ''}" onclick="L.view('need')">Was die Bridge braucht</button>
      <button type="button" class="${st.view === 'all' ? 'on' : ''}" onclick="L.view('all')">Alle gefundenen Werte (${all.length}${nChanged ? ', ' + nChanged + ' geändert' : ''})</button></div>`;
    const members = (s.members || []);
    const foot = `<div class="toolbar" style="margin-top:14px">
        <span class="hint">Gerät in der Oberfläche</span><select onchange="L.setRole(this.value)">${members.map(m =>
          `<option value="${esc(m.role)}" ${st.devRole === m.role ? 'selected' : ''}>${esc(m.name)} (${esc(ROLE(m.role))})</option>`).join('')}</select>
        <label class="hint"><input type="checkbox" ${st.ref ? 'checked' : ''} onchange="L.setRef(this.checked)"> nur Referenz (Bridge übernimmt nicht)</label></div>
      <div class="toolbar">
        <span class="hint">Profil-Schlüssel</span><code>${esc(st.d.key)}</code>
        <span class="hint">${st.d.key.startsWith('driver:') ? '(Treiber-Dateiname – gilt für alle Geräte dieses Treibers)' : '(Proxy – gilt für alle Geräte dieses Proxys)'}</span>
        <button class="act primary spacer" onclick="L.save()" ${missing.length ? `title="fehlt: ${esc(missing.join(', '))}"` : ''}>Profil speichern &amp; übernehmen</button>
        <button class="act" onclick="L.evaluate()">Prüfen</button>
        <span class="hint" id="lmsg">${esc(st.msg)}</span></div>
      ${missing.length ? `<p class="hint" style="color:var(--warn)">Pflichtfeld noch offen: ${esc(missing.join(', '))}</p>` : ''}
      ${st.ev ? evHtml() : ''}`;
    if (st.view === 'all') return views + allHtml(sch, all) + foot;
    return views + `<div class="toolbar"><label class="hint">Gerätetyp</label>
        <select onchange="L.setType(this.value)">${Object.entries(schemas).map(([k, x]) => `<option value="${k}" ${st.type === k ? 'selected' : ''}>${esc(x.label)}${sc[k] != null ? ' · ' + sc[k] : ''}</option>`).join('')}</select>
        <span class="hint">Vorschlag: <b>${esc((schemas[st.d.suggest.type] || {}).label || '')}</b> (Punkte aus Proxy, Treibername, Variablen und Befehlen)</span>
        <button class="act spacer" onclick="L.redo()">Vorschlag neu übernehmen</button></div>
      ${!all.some(c => c.src === 'event') ? `<p class="hint" style="margin-top:0">Tipp: Erst unter „Mitschnitt“ das Gerät bedienen – dann stehen hier auch Push-Felder zur Wahl (★ = passt zum Namen).</p>` : ''}
      ${Object.keys(sch.fields).length ? `<h3 class="lsub">Was die Bridge braucht – Werte</h3><table>${Object.entries(sch.fields).map(([f, x]) => fieldRow(f, x)).join('')}</table>`
        : `<p class="hint">Info-Gerät: keine Pflichtwerte – Werte unter „Alle gefundenen Werte“ als Info zuordnen.</p>`}
      ${Object.keys(sch.actions).length ? `<h3 class="lsub">Aktionen</h3><table>${Object.entries(sch.actions).map(([a, x]) => actRow(a, x)).join('')}</table>` : ''}
      <p class="hint">Zusatz-Infos (Temperatur, Batterie, Leistung …): ${st.sel.infos.length ? st.sel.infos.map(i => esc(i.label || i.key)).join(', ') : 'keine'} – zuordnen unter „Alle gefundenen Werte“.</p>`
      + foot;
  }
  // ---- Alle gefundenen Werte: jede Quelle einer Verwendung zuordnen (Feld, Info-Art oder nichts) + Umrechnung
  const TF = { '': 'keine', contains: 'enthält Text → Wert', seconds: 'Zeit (h:mm:ss) → Sekunden', factor: '× Faktor', invert: 'umkehren (an ↔ aus)' };
  function usageOf(c) {
    const k = cid(c);
    const f = Object.entries(st.sel.fields).find(([, x]) => cid(x) === k);
    if (f) return { u: 'f:' + f[0], spec: f[1] };
    const i = st.sel.infos.find(x => cid(x) === k);
    return i ? { u: 'i:' + i.kind, spec: i } : { u: '', spec: null };
  }
  function tfOf(spec) { const t = (spec && spec.tf) || {}; return t.contains ? 'contains' : t.seconds ? 'seconds' : t.factor ? 'factor' : t.invert ? 'invert' : ''; }
  function allHtml(sch, all) {
    const kinds = (st.sug && st.sug.kinds) || {};
    const empty = c => c.value == null || c.value === '' || /^-{2,}.*-{2,}$/.test(c.key);
    const rows = all.slice().sort((a, b) => (b.changed - a.changed) || (empty(a) - empty(b)) || ((b.seen || 0) - (a.seen || 0)) || a.key.localeCompare(b.key));
    return `<p class="hint" style="margin-top:0">Jede gefundene Quelle einer Verwendung zuordnen. <b>„geändert“</b> = hat sich während des Mitschnitts
        verändert (meist das Wichtige). Info-Werte erscheinen auf der Gerätekarte (Platz „Kachel“, höchstens 2) oder im Tooltip/Details.</p>
      <div class="scroll"><table><tr><th>Quelle</th><th>Wert / beobachtet</th><th>Verwendung</th><th>Bezeichnung · Platz</th><th>Umrechnung</th></tr>
      ${rows.map((c, n) => {
        const { u, spec } = usageOf(c), idx = all.indexOf(c), t = tfOf(spec);
        const tfv = spec && spec.tf ? (t === 'contains' ? (spec.tf.contains || []).map(x => x.join('=')).join('; ') : t === 'factor' ? spec.tf.factor : '') : '';
        return `<tr class="${c.changed ? 'lchg' : ''}"><td class="small">${c.changed ? '<span class="tag ok">geändert</span> ' : ''}${SRC[c.src]} <code>${esc(short(c.key, 48))}</code>
            <div class="hint">@ ${esc(ROLE(c.role))}${c.seen ? ' · ' + c.seen + '× gesehen' : ''}</div></td>
          <td class="small">${esc(short(c.value, 40))}${(c.values || []).length > 1 ? `<div class="hint">${esc((c.values || []).map(v => short(v, 18)).join(' · '))}</div>` : ''}</td>
          <td><select onchange="L.setUse(${idx}, this.value)"><option value="">–</option>
            <optgroup label="Funktion (${esc(sch.label || st.type)})">${Object.entries(sch.fields).map(([f, x]) => `<option value="f:${f}" ${u === 'f:' + f ? 'selected' : ''}>${esc(x.label)}</option>`).join('')}</optgroup>
            <optgroup label="Info anzeigen">${Object.entries(kinds).map(([k, x]) => `<option value="i:${k}" ${u === 'i:' + k ? 'selected' : ''}>${esc(x.label)}${x.unit ? ' (' + esc(x.unit) + ')' : ''}${!u && c.kind === k && k !== 'text' ? ' ★' : ''}</option>`).join('')}</optgroup></select></td>
          <td>${u.startsWith('i:') ? `<input style="width:150px" value="${esc(spec.label || '')}" onchange="L.setInfo(${idx}, 'label', this.value)">
            <select onchange="L.setInfo(${idx}, 'place', this.value)"><option value="card" ${spec.place === 'card' ? 'selected' : ''}>Kachel</option><option value="detail" ${spec.place !== 'card' ? 'selected' : ''}>Details</option></select>
            ${spec.kind === 'battery' ? `<input type="number" style="width:60px" title="Warnung unter %" value="${esc(spec.warn || 20)}" onchange="L.setInfo(${idx}, 'warn', +this.value)">` : ''}` : ''}</td>
          <td>${u ? `<select onchange="L.setTf(${idx}, this.value, '')">${Object.entries(TF).map(([k, l]) => `<option value="${k}" ${t === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
            ${t === 'contains' || t === 'factor' ? `<input style="width:140px" placeholder="${t === 'contains' ? 'Pause=play; Play=pause' : '0.1'}" value="${esc(tfv)}" onchange="L.setTf(${idx}, '${t}', this.value)">` : ''}` : ''}</td></tr>`;
      }).join('')}</table></div>`;
  }
  function setUse(idx, v) {
    const c = (st.sug.all || [])[idx]; if (!c) return;
    const k = cid(c), old = usageOf(c);
    for (const [f, x] of Object.entries(st.sel.fields)) if (cid(x) === k) delete st.sel.fields[f];
    st.sel.infos = st.sel.infos.filter(x => cid(x) !== k);
    const base = { src: c.src, role: c.role, key: c.key, ...(old.spec && old.spec.tf ? { tf: old.spec.tf } : {}) };
    if (v.startsWith('f:')) st.sel.fields[v.slice(2)] = base;
    else if (v.startsWith('i:')) {
      const kind = v.slice(2), nice = c.key.replace(/^prop:|^event:/, '').split('.').slice(-1)[0];
      st.sel.infos.push({ ...base, kind, label: (old.spec && old.spec.label) || nice, place: ['temperature', 'humidity', 'power', 'battery'].includes(kind) ? 'card' : 'detail' });
    }
    st.ev = null; render();
  }
  function setInfo(idx, k, v) { const c = (st.sug.all || [])[idx]; const i = c && st.sel.infos.find(x => cid(x) === cid(c)); if (i) i[k] = v; }
  function setTf(idx, kind, param) {
    const c = (st.sug.all || [])[idx]; if (!c) return;
    const { spec } = usageOf(c); if (!spec) return;
    if (!kind) delete spec.tf;
    else if (kind === 'contains') spec.tf = { contains: String(param || '').split(';').map(x => x.split('=').map(y => y.trim())).filter(x => x[0]) };
    else if (kind === 'factor') spec.tf = { factor: param || 1 };
    else spec.tf = { [kind]: true };
    st.ev = null; render();
  }
  function setRole(r) { st.devRole = r; }
  function setRef(v) { st.ref = v; }
  function view(v) { st.view = v; render(); }
  function evHtml() {
    const e = st.ev;
    return `<div class="sect"><h2 class="sec">Prüfung – so sieht die Bridge das Gerät jetzt${e.applied ? ' <span class="tag ok">übernommen</span>' : e.reference ? ' <span class="tag">Referenz</span>' : ''}</h2>
      ${!e.applied && !e.reference && e.skip ? `<p class="hint" style="color:var(--warn)">Nicht übernommen: ${esc(e.skip)}</p>` : ''}<table>
      ${Object.entries(e.fields).map(([f, x]) => `<tr><th style="width:22%">${esc(f)}</th><td><b>${esc(short(x.value, 80))}</b>${x.raw !== x.value ? ` <span class="hint">(roh: ${esc(short(x.raw, 40))})</span>` : ''}${x.guess ? ` <span class="tag warn">${esc(x.guess)}</span>` : ''}</td>
        <td class="hint">${esc(x.from)}</td></tr>`).join('')}
      ${(e.infos || []).map(i => `<tr><th>${esc(i.label)}</th><td><b>${esc(short(i.value, 60))}</b>${i.unit ? ' ' + esc(i.unit) : ''}${i.warn ? ' <span class="tag warn">Warnung</span>' : ''}</td>
        <td class="hint">Info · ${esc(i.kind)} · ${i.place === 'card' ? 'Kachel' : 'Details'}</td></tr>`).join('')}
      ${Object.entries(e.actions).map(([a, x]) => `<tr><th>${esc(a)}</th><td><code>${esc(x.command || '')}</code>${x.param ? ` <span class="hint">Wert → ${esc(x.param)}</span>` : ''}</td>
        <td><button class="act" onclick="L.test('${a}', ${x.param ? 'true' : 'false'})">Testen …</button></td></tr>`).join('')}</table>
      <p class="hint">„Testen“ schickt den Befehl wirklich an das Gerät (Schreibaktion, Rückfrage vorher).</p></div>`;
  }
  const findC = v => (st.sug.all || []).find(c => cid(c) === v);
  function setField(f, v) {
    if (!v) delete st.sel.fields[f];
    else { const c = findC(v) || (st.sug.fields[f] || []).find(x => cid(x) === v); st.sel.fields[f] = { src: c.src, role: c.role, key: c.key, ...(c.map ? { map: { ...c.map } } : {}) }; }
    st.ev = null; render();
  }
  function setMap(f, val, e, el) {
    const x = st.sel.fields[f]; if (!x) return;
    if (e === '__own') {                      // mehrstufig: z. B. Fenstergriff „gekippt“ (zählt als offen/aktiv)
      e = (prompt('Name des Zustands (z. B. gekippt):') || '').trim();
      if (!e) { if (el) el.value = ''; return; }
    }
    x.map = { ...(x.map || {}), [val]: e };
    if (el && e && ![...el.options].some(o => o.value === e)) render();
  }
  function setAction(a, v) {
    if (!v) { delete st.sel.actions[a]; render(); return; }
    const [role, ...rest] = v.split('|'), command = rest.join('|');
    const c = (st.sug.commands || []).find(x => x.role === role && x.command === command) || { params: [] };
    st.sel.actions[a] = { role, command, ...tgt(c), ...((schemas[st.type].actions[a] || {}).param ? { param: (c.params[0] || {}).name || '' } : {}) };
    st.ev = null; render();
  }
  function setParam(a, p) { if (st.sel.actions[a]) st.sel.actions[a].param = p; }
  async function setType(t) { st.type = t; st.ev = null; await suggest(true); render(); }
  async function redo() { await suggest(true); st.ev = null; render(); }
  async function save() {
    // Werte-Zuordnungen ohne Auswahl nicht speichern
    const fields = {};
    for (const [f, x] of Object.entries(st.sel.fields)) {
      const m = Object.fromEntries(Object.entries(x.map || {}).filter(([, e]) => e));
      fields[f] = { src: x.src, role: x.role, key: x.key, ...(Object.keys(m).length ? { map: m } : {}) };
    }
    try {
      const r = await post('/api/learn/profile', { item_id: st.id, key: st.d.key, type: st.type, fields, actions: st.sel.actions,
        infos: st.sel.infos, device_role: st.devRole, reference: st.ref, name: st.d.name });
      st.msg = st.ref ? 'gespeichert ✓ (nur Referenz – nicht übernommen)' : r.mine ? 'gespeichert ✓ – als Gerät in die Bridge übernommen'
        : `gespeichert ✓ – NICHT übernommen: ${r.skip || 'kein passendes Item'}`;
      if (typeof loadDevices === 'function') loadDevices();
      await evaluate();
      if (typeof loadDevices === 'function') { allItems = await api('/api/learn/items').catch(() => allItems); }
    } catch (e) { st.msg = e.message; render(); }
  }
  async function evaluate() {
    try { st.ev = await api(`/api/learn/evaluate/${st.id}?key=${encodeURIComponent(st.d.key)}`); }
    catch (e) { st.msg = e.message.includes('kein Profil') ? 'erst speichern' : e.message; }
    render();
  }
  async function test(a, needsValue) {
    let value = null;
    if (needsValue) { value = prompt(`Wert für „${a}“ (z. B. 20):`); if (value == null) return; }
    if (!confirm(`Schreibaktion: „${a}“ jetzt wirklich an das Gerät senden?`)) return;
    try { const r = await post(`/api/learn/test/${st.id}`, { action: a, value, key: st.d.key }); st.msg = `gesendet: ${r.command} → #${r.target}`; }
    catch (e) { st.msg = e.message; }
    setTimeout(evaluate, 1500); render();
  }

  return { open, close, tab, view, pickSearch, rescan, delProfile, capStart, capStop, setField, setMap, setAction, setParam, setType, redo, save,
    evaluate, test, setUse, setInfo, setTf, setRole, setRef };
})();
