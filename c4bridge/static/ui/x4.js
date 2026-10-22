/* Theme OS X4 — rendert nur; Zustand/Aktionen kommen aus ui/core.js (C4). */
'use strict';
const X = (() => {
  const S = C4.S, t = C4.t;
  let view = 'home';                 // home | house | np | cat:<bereich>
  let lightScope = 'room', lightTab = 'lights', house = null;
  let mb = { mode: 'album', artist: null, genre: null, q: '', albumId: null }, mbSeq = 0, searchTimer;
  let dragging = false, menuOpen = false;

  const fa = f => (typeof f === 'number' ? f : `'${f}'`);
  const modeLbl = m => { const v = t('mode_' + m); return v === 'mode_' + m ? m : v; };
  const blindTxt = b => (b.moving ? t('moving') : b.known ? t('pct_open', b.level) : t('pos_unknown'));
  const rng = (val, data) => `<input type="range" class="rng" min="0" max="100" value="${val}" style="--p:${val}%" ${data} onclick="event.stopPropagation()">`;
  const favBtn = f => `<button class="fav ${C4.isFav(f) ? 'have' : ''}" title="${t('add_fav')}" onclick="event.stopPropagation();C4.toggleFav(${fa(f)})">${ic('star', 16)}</button>`;

  // ------------------------------------------------------------ Gerüst
  function mount() {
    document.body.classList.add('x4bg');
    $('#app').innerHTML = `<div class="x4">
      <nav class="rail" id="rail"></nav>
      <header class="top"><button class="room" id="roombtn"></button><span class="greet" id="greet"></span>
        <div class="clock" id="clock"></div>
        <button class="kebab" id="cbbtn" title="${t('custom_buttons')}" onclick="X.cbOpen(event)" hidden>${ic('menu', 22)}</button>
        <button class="kebab" title="${t('more')}" onclick="X.menu(event)">${ic('kebab', 22)}</button></header>
      <div class="pills" id="pills"></div>
      <main class="view" id="view"></main>
      <div class="np" id="np" hidden></div>
      <div class="wxb" id="wxb" hidden></div>
    </div><div class="menu" id="menu" hidden></div><div class="cbp" id="cbp" hidden></div>`;
    document.addEventListener('click', e => {
      if (menuOpen && !e.target.closest('#menu, .kebab')) closeMenu();
      if (cbOpenNow && !e.target.closest('#cbp, #cbbtn, #c4ask')) cbClose();
    });
    document.addEventListener('pointerdown', e => { if (e.target.matches && e.target.matches('.rng')) dragging = true; });
    document.addEventListener('pointerdown', gripDrag);
    document.addEventListener('pointerdown', sortDrag);
    document.addEventListener('pointerup', () => setTimeout(() => { dragging = false; }, 400));
    document.addEventListener('input', e => {
      if (e.target.matches('.rng')) C4.rangeLive(e.target);
      if (e.target.id === 'mq') {
        clearTimeout(searchTimer);
        const q = e.target.value.trim();
        searchTimer = setTimeout(() => { mb = { ...mb, q, mode: q ? 'search' : 'album' }; renderMusic(true); }, 300);
      }
    });
    document.addEventListener('change', e => {
      const r = e.target;
      if (!r.matches('.rng')) return;
      dragging = false;
      C4.rangeEnd(r);
      if (r.dataset.lvl) C4.setLevel(+r.dataset.lvl, +r.value).then(() => { if (lightScope === 'house') loadHouseLights(); });
      else if (r.dataset.blind) C4.blindLevel(+r.dataset.blind, +r.value);
      else if (r.dataset.vol) C4.volume(+r.value);
    });
    tickClock(); setInterval(tickClock, 10000);
    // Direktsprung per Anker, z. B. /#cat:light, /#house, /#np, /#edit (praktisch für Wandtablets)
    const h = decodeURIComponent(location.hash.slice(1));
    if (h === 'edit') setTimeout(() => C4.setEditing(true), 0);
    else if (/^(house|np|cat:[a-z]+)$/.test(h)) view = h;
  }
  function tickClock() {
    const c = $('#clock'); if (c) c.innerHTML = `<b>${C4.fmtTime()}</b><span>${esc(C4.fmtDate())}</span>`;
  }

  function go(v) {
    view = v; closeMenu();
    if (v === 'cat:light' && lightScope === 'house') loadHouseLights();
    renderAll();
    const el = $('#view'); if (el) el.scrollTop = 0;
  }
  function renderAll() { renderTop(); renderRail(); renderPills(); renderView(true); renderNp(); }

  // ------------------------------------------------------------ Rahmen
  function renderTop() {
    const b = $('#roombtn');
    if (view === 'house') { b.innerHTML = esc(t('house')); b.onclick = () => go('home'); }
    else if (view === 'home') { b.innerHTML = `${esc(S.room.name)} ${ic('chev_right', 22)}`; b.onclick = () => go('house'); }
    else { b.innerHTML = `${ic('chev_left', 24)} ${esc(S.room.name)}`; b.onclick = () => go('home'); }
    $('#greet').textContent = view === 'home' ? C4.greeting() : '';
    const cb = $('#cbbtn'); if (cb) cb.hidden = !(S.buttons && S.buttons.length);
    $('#pills').hidden = view !== 'home';
  }
  function renderRail() {
    const cats = C4.cats().map(c => c.key);
    const nOpen = C4.devsOf('contact').filter(d => C4.sec(d).active).length;
    const items = [['home', 'home', t('room')], ['house', 'house', t('house')]];
    if (cats.includes('listen')) items.push(['cat:listen', 'music', t('cat_listen')]);
    if (cats.includes('watch')) items.push(['cat:watch', 'tv', t('cat_watch')]);
    if (cats.includes('security')) items.push(['cat:security', 'shield', t('cat_security')]);
    if (cats.includes('camera')) items.push(['cat:camera', 'camera', t('cat_camera')]);
    $('#rail').innerHTML = items.map(([v, i, l]) => `<button class="${view === v ? 'on' : ''}" title="${esc(l)}" onclick="X.go('${v}')">${ic(i, 26)}${v === 'cat:security' && nOpen ? `<span class="bdg">${nOpen}</span>` : ''}</button>`).join('');
    // Einstellungen gibt es nur über das Drei-Punkte-Menü oben rechts
  }
  function renderPills() {
    if (view !== 'home') return;
    const np = C4.nowPlaying();
    $('#pills').innerHTML = C4.cats().map(c => {
      const act = C4.catActive(c.key);
      const alert = c.key === 'security' && act && C4.summary('security') !== t('motion');
      const media = (c.key === 'listen' || c.key === 'watch') && act;
      const useImg = np.img && (np.kodi || (c.key === 'listen' && !np.watch));
      const icon = media ? C4.miniArt(useImg ? np.img : '', useImg ? 0 : S.rs.selected_device, c.icon, 32) : ic(c.icon, 17);
      return `<button class="pl ${alert ? 'alert' : act ? 'act' : ''}" onclick="X.go('cat:${c.key}')"><span class="pi ${media ? 'art' : ''}">${icon}</span>
        <span class="pt"><small>${t('cat_' + c.key)}</small><b>${esc(C4.summary(c.key))}</b></span></button>`;
    }).join('');
  }

  // ------------------------------------------------------------ Inhalt
  function renderView(force) {
    const el = $('#view'); if (!el || !S.room) return;
    if (!force) {
      if (dragging) return;
      const a = document.activeElement;
      if (a && el.contains(a) && a.matches('input')) return;
      if (view === 'cat:listen') { updateSources(); return; }
      if (view === 'cat:watch' || (view === 'np' && el.querySelector('.rmt'))) { updateWatch(); return; }
      if (view === 'np') { refreshNp(); return; }
      if (view === 'house') return;
    }
    if (view === 'home') el.innerHTML = homeHtml();
    else if (view === 'house') renderHouse();
    else if (view === 'np') el.innerHTML = npHtml();
    else if (view === 'cat:watch') el.innerHTML = watchHtml();
    else if (view === 'cat:listen') { el.innerHTML = `<div class="sheet">${musicHtml()}</div>`; renderMusic(true); }
    else if (view.startsWith('cat:')) el.innerHTML = `<div class="sheet">${catHtml(view.slice(4))}</div>`;
    C4.wireCams(el);
    sizeGrids(el);
  }
  function gridGeo(g) {
    const cs = getComputedStyle(g), gap = parseFloat(cs.columnGap) || 12, rh = parseFloat(cs.gridAutoRows) || 84;
    const cols = Math.max(1, cs.gridTemplateColumns.split(' ').filter(Boolean).length);
    return { gap, rh, cols, cw: (g.clientWidth - gap * (cols - 1)) / cols };
  }
  const camRows = (geo, w) => Math.max(1, Math.round(((w * geo.cw + (w - 1) * geo.gap) * 9 / 16 + geo.gap) / (geo.rh + geo.gap)));
  function sizeGrids(root) {
    root.querySelectorAll('.grid').forEach(g => {
      const geo = gridGeo(g);
      const half = Math.max(1, Math.floor(geo.cols / 2));
      g.querySelectorAll(':scope > .t').forEach(el => {
        const dw = el.dataset.w, w = dw === 'half' ? half : dw ? Math.min(+dw, geo.cols) : null;
        el.style.gridColumn = w ? `span ${w}` : '';
        if (el.classList.contains('cam')) el.style.gridRow = `span ${camRows(geo, w || Math.min(2, geo.cols))}`;
        else if (el.dataset.h === 'cam') el.style.gridRow = `span ${camRows(geo, half)}`;
        else el.style.gridRow = el.dataset.h ? `span ${el.dataset.h}` : '';
      });
    });
  }

  // ---- Startseite (Favoriten-Kacheln) ----
  function homeHtml() {
    let h = '';
    if (S.editing) {
      h += `<div class="editbar"><button class="btn pri" onclick="C4.setEditing(false)">${t('done')}</button>
        <button class="btn" onclick="C4.showPicker()">${ic('plus', 16)} ${t('add_fav')}</button>
        ${S.layout.can_set_default && !S.me.open_mode ? `<button class="btn" onclick="C4.saveAsDefault()">${t('save_default')}</button>` : ''}
        ${S.layout.personal ? `<button class="btn" onclick="C4.resetLayout()">${t('reset_layout')}</button>` : ''}
        <span class="hint">${t('edit_hint')}</span></div>`;
    }
    // Kompakt: „Läuft gerade" steht schon rechts im Player
    let favs = C4.favList();
    if (cmp() && !S.editing && C4.nowPlaying().on) favs = favs.filter(f => f !== 'media');
    autoSizes(favs);
    h += `<div class="grid${S.editing ? ' ed' : ''}">${favs.map(tile).join('')}
      ${S.editing ? `<div class="t add" onclick="C4.showPicker()">${ic('plus', 22)} ${t('add_fav')}</div>` : ''}</div>`;
    if (wide()) h += overviewHtml();
    return h;
  }
  // Raum ohne eigene Größen (PC breit): Kamera halb so breit wie das Raster, daneben wird der Favorit der größten
  // Gerätegruppe (Rollläden/Licht/Schalter) zur Gruppen-Kachel mit allen Geräten dieser Art, so hoch wie die Kamera.
  // Es sind normale Kachelgrößen – im Bearbeiten-Modus sieht alles gleich aus; beim ersten Ändern werden sie gespeichert.
  let autoSz = {};
  function autoSizes(favs) {
    autoSz = {};
    const saved = (S.layout.sizes || {})[S.room.id];
    if (!wide() || (saved && Object.keys(saved).length)) return;
    const cam = favs.find(isCam); if (cam === undefined) return;
    autoSz[cam] = { w: 'half' };
    const g = GRP.map(k => [k, favs.find(f => (C4.dev(f) || {}).type === k)]).filter(([k, f]) => f !== undefined && C4.devsOf(k).length >= 2)
      .sort((a, b) => C4.devsOf(b[0]).length - C4.devsOf(a[0]).length)[0];
    if (g) autoSz[g[1]] = { w: 'half', h: 'cam' };
  }
  const effSize = f => C4.favSize(f) || autoSz[f] || null;
  const isGrp = (sz, d) => !!sz && (sz.h === 'cam' || sz.h >= 2) && GRP.includes(d.type);
  // automatische Größen beim ersten eigenen Ändern festschreiben (sonst springt das Layout)
  function persistAuto() {
    if (!Object.keys(autoSz).length) return;
    const z = {}, n = v => +((v || '').match(/\d+/) || [1])[0];
    document.querySelectorAll('#view .grid > .t[data-f]').forEach(el => {
      const f = el.dataset.f; if (autoSz[f]) z[f] = { w: n(el.style.gridColumn), h: n(el.style.gridRow) };
    });
    const all = S.layout.sizes = S.layout.sizes || {};
    all[S.room.id] = { ...z, ...(all[S.room.id] || {}) };
    autoSz = {};
  }
  const isCam = f => typeof f === 'number' && (C4.dev(f) || C4.camDev(f) || {}).type === 'camera';
  // PC breit: unter den Favoriten der ganze Raum nach Bereichen (nur Bereiche, die es im Raum gibt;
  // Favoriten nicht doppelt). Wenige Geräte → Kacheln breiter, damit kein Loch bleibt.
  function overviewHtml() {
    const fav = new Set(C4.favList().filter(f => typeof f === 'number'));
    const grpKinds = new Set();             // Art mit Gruppen-Kachel: die Kachel zeigt sie, Rest über den Bereich
    for (const f of C4.favList()) {
      const d = typeof f === 'number' && C4.dev(f), sz = d && effSize(f);
      if (d && isGrp(sz, d)) grpKinds.add(d.type);
    }
    if (C4.favList().includes('cat:thermostat')) {   // Klima-Favorit zeigt bei einem Thermostat schon das Gerät
      const th = C4.devsOf('thermostat').filter(d => S.clim[d.id] || S.temps[d.id] != null);
      if (th.length === 1) fav.add(th[0].id);
    }
    const pick = list => list.filter(d => !fav.has(d.id));
    let secs = [['light', pick(C4.devsOf('light'))], ['blind', pick(C4.devsOf('blind'))],
      ['thermostat', pick(C4.devsOf('thermostat').filter(d => S.clim[d.id] || S.temps[d.id] != null))],
      ['switch', pick(C4.devsOf('switch'))], ['camera', pick(C4.camList())]].filter(([k, l]) => l.length && !grpKinds.has(k));
    return secs.map(([k, l]) => `<div class="ovh" onclick="X.go('cat:${k}')">${t('cat_' + k)} <span>${l.length}</span></div>
      <div class="grid ov ${l.length <= 2 ? 'few' : ''}">${l.map(d => tile(d.id)).join('')}</div>`).join('');
  }
  // Gerätezeilen einer Gruppen-Kachel: Symbol, Name, (Schieber), Zustand, Tasten
  function grpRows(k, list) {
    return list.map(d => {
      if (k === 'blind') {
        const b = C4.blind(d);
        return `<div class="gr ${b.moving ? 'on' : ''}"><span class="gi">${ic(b.open ? 'blind_open' : 'blind_closed', 18)}</span><span class="gn" onclick="C4.blindToggle(${d.id})">${esc(C4.label(d))}</span>
          ${b.hasLevel ? rng(b.level, `data-blind="${d.id}"`) : ''}<small>${b.moving || b.known ? blindTxt(b) : b.group ? t('group') : '–'}</small>
          <span class="bb"><button title="↑" onclick="C4.blindDo(${d.id},'open')">${ic('chev_up', 16)}</button>
            <button title="■" onclick="C4.blindDo(${d.id},'stop')">${ic('stop', 14)}</button>
            <button title="↓" onclick="C4.blindDo(${d.id},'close')">${ic('chev_down', 16)}</button></span></div>`;
      }
      if (k === 'light') {
        const l = C4.light(d), lvl = l.on ? 0 : 100;
        return `<div class="gr ${l.on ? 'on' : ''}"><span class="gi">${ic(l.on ? 'light_on' : 'light_off', 18)}</span><span class="gn" onclick="X.lt(${d.id},${lvl})">${esc(C4.label(d))}</span>
          ${l.dimmable ? rng(l.level, `data-lvl="${d.id}"`) : ''}<small>${l.on ? (l.dimmable && l.level != null ? l.level + ' %' : t('on')) : t('off')}</small>
          <button class="sw ${l.on ? 'on' : ''}" onclick="X.lt(${d.id},${lvl})"></button></div>`;
      }
      const s = C4.sw(d);
      return `<div class="gr ${s.on ? 'on' : ''}"><span class="gi">${ic('power', 18)}</span><span class="gn" onclick="C4.switchToggle(${d.id})">${esc(C4.label(d))}</span>
        <small>${s.known ? (s.on ? t('on') : t('off')) : t('sw_unknown')}</small>
        <button class="sw ${s.on ? 'on' : ''}" onclick="C4.switchToggle(${d.id})"></button></div>`;
    }).join('');
  }
  // Bearbeiten: Griff unten rechts zieht die Kachel in ganzen Rasterzellen größer/kleiner (Kamera: nur Breite, Höhe = 16:9)
  function gripDrag(e) {
    const gp = e.target.closest && e.target.closest('[data-grip]'); if (!gp || !S.editing) return;
    e.preventDefault(); e.stopPropagation();
    const el = gp.closest('.t'), g = el.parentElement, geo = gridGeo(g), r = el.getBoundingClientRect();
    const f = /^\d+$/.test(gp.dataset.grip) ? +gp.dataset.grip : gp.dataset.grip, cam = el.classList.contains('cam');
    let w = 0, h = 0;
    dragging = true; el.classList.add('drag'); gp.setPointerCapture(e.pointerId);
    const move = ev => {
      w = Math.max(1, Math.min(geo.cols, Math.round((ev.clientX - r.left + geo.gap) / (geo.cw + geo.gap))));
      h = cam ? camRows(geo, w) : Math.max(1, Math.min(8, Math.round((ev.clientY - r.top + geo.gap) / (geo.rh + geo.gap))));
      el.style.gridColumn = `span ${w}`; el.style.gridRow = `span ${h}`;
    };
    const up = () => {
      gp.removeEventListener('pointermove', move); gp.removeEventListener('pointerup', up); gp.removeEventListener('pointercancel', up);
      el.classList.remove('drag'); dragging = false;
      if (w) { persistAuto(); C4.setFavSize(f, { w, h }); }
    };
    gp.addEventListener('pointermove', move); gp.addEventListener('pointerup', up); gp.addEventListener('pointercancel', up);
  }
  // Bearbeiten: Kachel greifen und verschieben – die anderen weichen aus; Reihenfolge wird beim Loslassen gespeichert
  function sortDrag(e) {
    const el = S.editing && e.target.closest && e.target.closest('#view .grid.ed > .t[data-f]');
    if (!el || e.button > 0 || e.target.closest('button, input, [data-grip]')) return;
    const g = el.parentElement, x0 = e.clientX, y0 = e.clientY;
    let on = false;
    const move = ev => {
      if (!on && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 8) return;
      if (!on) { on = true; dragging = true; el.classList.add('drag', 'mvg'); }
      el.style.pointerEvents = 'none';
      const hit = document.elementFromPoint(ev.clientX, ev.clientY), tg = hit && hit.closest('.t[data-f]');
      el.style.pointerEvents = '';
      if (!tg || tg === el || tg.parentElement !== g) return;
      const r = tg.getBoundingClientRect(), after = ev.clientX > r.left + r.width / 2;
      g.insertBefore(el, after ? tg.nextSibling : tg);
    };
    const up = () => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
      document.removeEventListener('pointercancel', up);       // sonst bliebe dragging=true → keine Live-Updates mehr
      if (!on) return;
      el.classList.remove('drag', 'mvg'); dragging = false;
      const order = [...g.querySelectorAll(':scope > .t[data-f]')].map(x => /^\d+$/.test(x.dataset.f) ? +x.dataset.f : x.dataset.f);
      persistAuto(); C4.setFavOrder(order);
    };
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
    document.addEventListener('pointercancel', up);
  }
  // Zahnrad: welche Geräte gleicher Art die Gruppen-Kachel zeigt
  function grpPick(f) {
    const d = C4.dev(f); if (!d) return;
    const all = C4.devsOf(d.type), cur = grpDevs(f, d, effSize(f)).map(x => x.id), mode = (effSize(f) || {}).mode || 'auto';
    const m = document.createElement('div'); m.className = 'gpick';
    m.innerHTML = `<div class="box" role="dialog" aria-label="${t('grp_pick')}"><h3>${t('grp_pick')}</h3>
      ${all.map(x => `<label><input type="checkbox" value="${x.id}" ${cur.includes(x.id) ? 'checked' : ''}> ${esc(C4.label(x))}</label>`).join('')}
      <h3 class="gsub">${t('grp_show')}</h3><div class="seg gmode">${[['auto', 'grp_auto'], ['btn', 'grp_btn'], ['slider', 'grp_sl'], ['both', 'grp_both']]
        .map(([v, k]) => `<label><input type="radio" name="gmode" value="${v}" ${mode === v ? 'checked' : ''}> ${t(k)}</label>`).join('')}</div>
      <div class="act"><button class="btn" data-x>${t('r_cancel')}</button><button class="btn pri" data-ok>${t('apply')}</button></div></div>`;
    document.body.appendChild(m);
    m.addEventListener('click', ev => {
      if (ev.target.closest('[data-ok]')) {
        const sel = [...m.querySelectorAll('input[type=checkbox]:checked')].map(i => +i.value);
        const md = (m.querySelector('input[name=gmode]:checked') || {}).value || 'auto';
        m.remove(); persistAuto(); C4.setFavSize(f, { sel: sel.length ? sel : [d.id], mode: md });
      } else if (ev.target.closest('[data-x]') || ev.target === m) m.remove();
    });
  }
  // Gruppen-Kachel: Favorit (Licht/Rollladen/Schalter) ab 2 Zeilen Höhe zeigt mehrere Geräte; Zahnrad wählt sie
  const GRP = ['light', 'blind', 'switch'];
  function grpDevs(f, d, sz) {
    const all = C4.devsOf(d.type);
    if (!sz || !Array.isArray(sz.sel)) return all;
    const l = sz.sel.map(id => all.find(x => x.id === id)).filter(Boolean);
    return l.length ? l : [d];
  }
  function grpTile(f, d, sz) {
    const l = grpDevs(f, d, sz);
    return `<div class="gth"><b>${t('cat_' + d.type)}</b><small>${t('grp_n', l.length)}</small>
        <button class="gear" title="${t('grp_pick')}" onclick="event.stopPropagation();X.grpPick(${f})">${ic('gear', 16)}</button></div>
      <div class="gtr" style="--n:${l.length}">${grpRows(d.type, l)}</div>`;
  }
  function tile(f) {
    const sz = effSize(f);
    const ed = `<button class="ed rm" onclick="event.stopPropagation();C4.rmFav(${fa(f)})">${ic('x', 14)}</button>
      <span class="ed mv"><button onclick="event.stopPropagation();C4.moveFav(${fa(f)},-1)">◀</button><button onclick="event.stopPropagation();C4.moveFav(${fa(f)},1)">▶</button></span>
      <span class="ed grip" data-grip="${esc(String(f))}" title="${t('grp_size')}"></span>`;
    const wrap = (cls, inner, tap) => `<div class="t ${cls}" data-f="${esc(String(f))}"${sz && sz.w ? ` data-w="${sz.w}"` : ''}${sz && sz.h ? ` data-h="${sz.h}"` : ''} onclick="${S.editing ? '' : tap}">${inner}${ed}</div>`;
    const txt = (small, big) => `<span class="tx"><small>${esc(small)}</small><b>${esc(big)}</b></span>`;
    if (f === 'cat:thermostat') {      // ein Thermostat im Raum → Kachel direkt mit −/+ statt nur Zusammenfassung
      const th = C4.devsOf('thermostat').filter(d => S.clim[d.id] || S.temps[d.id] != null);
      if (th.length === 1) return tile(th[0].id);
    }
    if (typeof f === 'string' && f.startsWith('cat:')) {
      const k = f.slice(4), c = C4.cats().find(x => x.key === k);
      // Hören/Sehen aktiv: rechts das Cover der Musik bzw. das Icon der Quelle (TV, KODI …)
      let art = '';
      if ((k === 'listen' || k === 'watch') && C4.catActive(k)) {
        const np = C4.nowPlaying(), useImg = np.img && (np.kodi || (k === 'listen' && !np.watch));
        art = `<span class="tart">${C4.miniArt(useImg ? np.img : '', useImg ? 0 : S.rs.selected_device, c.icon, 64)}</span>`;
      }
      return wrap('w2' + (C4.catActive(k) ? ' on' : ''), `<span class="ti">${ic(c.icon, 28)}</span>${txt(t('cat_' + k), C4.summary(k))}${art}`, `X.go('cat:${k}')`);
    }
    if (f === 'media') {
      const np = C4.nowPlaying();
      const cov = np.on ? (np.img ? `<img class="mcov" src="${esc(np.img)}" onerror="this.remove()">` : C4.coverHtml(np.artist, np.title || np.source, 'cover mcov'))
        : `<span class="ti">${ic('music', 28)}</span>`;
      // „Läuft gerade" (Player) – unterscheidet sich so von der Bereichs-Kachel „Hören" (Musikauswahl)
      return wrap('w2' + (np.on ? ' on' : ''), cov + txt(t('now_playing'), np.on ? (np.title || np.source) : t('listen_start')), `X.go('${np.on ? 'np' : 'cat:listen'}')`);
    }
    if (f === 'security') {
      const a = C4.catActive('security');
      return wrap(a ? 'alert' : 'okc', `<span class="ti">${ic('shield', 28)}</span>${txt(t('cat_security'), C4.summary('security'))}`, `X.go('cat:security')`);
    }
    const d = C4.dev(f) || C4.camDev(f); if (!d) return '';
    if (isGrp(sz, d)) return wrap('grpt m-' + (sz.mode || 'auto'), grpTile(f, d, sz), '');
    if (d.type === 'light') {
      const l = C4.light(d), icn = `<span class="ti">${ic(l.on ? 'light_on' : 'light_off', 28)}</span>`;
      if (l.dimmable) return wrap('w2 slide' + (l.on ? ' on' : ''), icn + txt(l.on ? l.level + ' %' : t('off'), C4.label(d)) + rng(l.level, `data-lvl="${d.id}"`), `C4.toggleLight(${d.id})`);
      return wrap(l.on ? 'on' : '', icn + txt(l.on ? t('on') : t('off'), C4.label(d)), `C4.toggleLight(${d.id})`);
    }
    if (d.type === 'blind') {
      const b = C4.blind(d);
      const bt = b.moving || b.known ? blindTxt(b) : b.group ? t('group') : '–';
      // mit Position: Schieberegler wie beim Licht (fährt beim Loslassen); Gruppen ohne Position nur auf/zu
      if (b.hasLevel) return wrap('w2 slide' + (b.moving ? ' on' : ''), `<span class="ti">${ic(b.open ? 'blind_open' : 'blind_closed', 28)}</span>${txt(bt, C4.label(d))}`
        + rng(b.level, `data-blind="${d.id}"`), `C4.blindToggle(${d.id})`);
      return wrap(b.moving ? 'on' : '', `<span class="ti">${ic(b.open ? 'blind_open' : 'blind_closed', 28)}</span>${txt(bt, C4.label(d))}`, `C4.blindToggle(${d.id})`);
    }
    if (d.type === 'thermostat') {
      const c = C4.clim(d);
      return wrap('w2 therm' + (c.heating ? ' on' : ''), `<span class="ti" style="color:var(--warn)">${ic('flame', 28)}</span>
        <span class="tx"><small>${esc(climName(d) + ' · ' + (c.temp != null ? C4.num1(c.temp) + '°' : '–'))}</small>
          <b>${esc(modeLbl(c.mode || 'Off'))}${c.heating ? ` <span class="flm" title="${t('heating')}">${ic('flame', 16)}</span>` : ''}</b></span>
        <button class="pm" onclick="event.stopPropagation();C4.climStep(${d.id},-0.5)">−</button>
        <span class="big">${c.sp != null ? C4.num1(c.sp) : '–'}<small>°C</small></span>
        <button class="pm" onclick="event.stopPropagation();C4.climStep(${d.id},0.5)">+</button>`, `X.go('cat:thermostat')`);
    }
    if (d.type === 'switch') {
      const s = C4.sw(d);
      return wrap(s.on ? 'on' : '', `<span class="ti">${ic('power', 28)}</span>${txt(s.known ? (s.on ? t('on') : t('off')) : t('sw_unknown'), C4.label(d))}`, `C4.switchToggle(${d.id})`);
    }
    if (d.type === 'learned') {
      const L = C4.learned(d), info = (L.infos || []).find(i => i.place === 'card');
      const small = [lState(d, L), info ? C4.infoText(info) : ''].filter(Boolean).join(' · ') || t('l_learned');
      const tap = (L.actions || []).includes('toggle') ? `C4.learnedAct(${d.id},'toggle')`
        : (L.actions || []).includes('on') && L.active != null ? `C4.learnedAct(${d.id},'${L.active ? 'off' : 'on'}')` : '';
      return wrap(d.ltype === 'sensor' ? (L.active == null ? '' : L.active ? 'alert' : 'okc') : L.active ? 'on' : '',
        `<span class="ti">${ic({ sensor: L.active ? 'window_open' : 'window_closed', switch: 'power', info: 'info', media: 'music' }[d.ltype] || 'gear', 28)}</span>${txt(small, C4.label(d))}`, tap);
    }
    if (d.type === 'camera')
      return wrap('w2 h2 cam', `${C4.camHtml(d)}<span class="lbl">${S.cams[d.id] && S.cams[d.id].mode === 'rtsp' ? `<span class="live">${t('live')}</span>` : ''} ${esc(C4.label(d))}</span>`, `X.go('cat:camera')`);
    return '';
  }

  // Thermostat-Beschriftung: eigener Name (Notiz) oder – bei einem Thermostat im Raum – der Raumname
  const climName = d => d.note || (C4.devsOf('thermostat').length <= 1 ? S.room.name : d.name);

  // ---- Haus (alle Räume) ----
  async function renderHouse() {
    const el = $('#view');
    if (!el.querySelector('.houseg')) el.innerHTML = `<div class="houseg"><p class="empty">…</p></div>`;
    const data = await C4.house();
    if (view !== 'house') return;
    let h = '', floor = null;
    for (const g of C4.roomGroups()) {
      if (g.name !== floor || g.fav) { floor = g.name; h += `<div class="floorh">${g.fav ? ic('star', 12) + ' ' : ''}${esc(floor)}</div>`; }
      for (const r of g.rooms) {
        const x = data.find(z => z.room.id === r.id) || {};
        const st = [x.lightsOn ? `<span class="lit">${ic('light_on', 12)} ${t('n_on', x.lightsOn)}</span>` : '',
          x.media ? `<span class="med">${C4.miniArt(x.play && x.play.img, x.play && x.play.source, 'music', 18)} ${esc((x.play && x.play.title) || t('cat_listen'))}</span>` : '',
          x.temp != null ? `<span>${ic('thermostat', 12)} ${C4.num1(x.temp)}°</span>` : ''].join('');
        h += `<button class="rc ${r.id === S.room.id ? 'cur' : ''}" onclick="X.pickRoom(${r.id})"><span class="rch"><b>${esc(r.name)}</b>${C4.favStar(r.id)}</span>
          <div class="st">${st || `<span>${t('all_off')}</span>`}</div></button>`;
      }
    }
    el.innerHTML = `<div class="houseg">${h}</div>`;
  }
  async function pickRoom(id) { view = 'home'; await C4.loadRoom(id); }

  // ---- Bereichs-Seiten ----
  function catHtml(k) {
    const head = (title, extra = '') => `<div class="sh-h"><h2>${title}</h2>${extra}</div>`;
    const body = inner => `<div class="sh-b">${inner}</div>`;
    if (k === 'light') {
      const seg = `<div class="seg"><button class="${lightScope === 'room' ? 'on' : ''}" onclick="X.lightScope('room')">${t('room')}</button>
        <button class="${lightScope === 'house' ? 'on' : ''}" onclick="X.lightScope('house')">${t('house')}</button></div>`;
      const tabs = `<div class="tabs"><button class="${lightTab === 'lights' ? 'on' : ''}" onclick="X.lightTab('lights')">${t('light_sources')}</button>
        <button class="${lightTab === 'scenes' ? 'on' : ''}" onclick="X.lightTab('scenes')">${t('scenes')}</button></div>`;
      if (lightTab === 'scenes') return head(t('cat_light'), seg) + tabs + body(S.scenes.length
        ? `<div class="cards">${S.scenes.map(sc => `<div class="c ${sc.active ? 'on' : ''}" onclick="C4.sceneToggle(${sc.id})">
            <span class="ci">${ic(sc.active ? 'light_on' : 'light_off', 26)}</span>
            <span class="cx"><b>${esc(sc.name)}</b><small>${sc.active ? t('scene_on') : ''}</small></span></div>`).join('')}</div>`
        : `<p class="empty">${t('no_scenes')}</p>`);
      if (lightScope === 'room') return head(t('cat_light'), seg) + tabs + body(`<div class="cards">${C4.devsOf('light').map(d => lightCard(d, C4.light(d))).join('')}${lCards('l_light')}</div>`);
      if (!house) return head(t('cat_light'), seg) + tabs + body('<p class="empty">…</p>');
      let h = '';
      for (const r of S.rooms) {
        const ds = house.devs.filter(d => d.room_id === r.id);
        if (!ds.length) continue;
        h += `<div class="secth">${esc(r.name)}</div><div class="cards">${ds.map(d => {
          return lightCard(d, C4.light(d), true);
        }).join('')}</div>`;
      }
      return head(t('cat_light'), seg) + tabs + body(h);
    }
    if (k === 'blind') return head(t('cat_blind')) + body(`<div class="cards">${C4.devsOf('blind').map(blindCard).join('')}${lCards('l_blind')}</div>`);
    if (k === 'thermostat') return head(t('cat_thermostat')) + body(C4.devsOf('thermostat').map(climHtml).join('') + (C4.has('l_thermostat') ? `<div class="cards">${lCards('l_thermostat')}</div>` : ''));
    if (k === 'switch') return head(t('cat_switch')) + body(`<div class="cards">${C4.devsOf('switch').map(swCard).join('')}${lCards('l_switch')}</div>`);
    if (k === 'more') return head(t('cat_more')) + body(`<div class="cards">${lCards('l_info')}${lCards('l_media')}</div>`);
    if (k === 'security') {
      const sensors = [...C4.devsOf('contact'), ...C4.devsOf('motion')], relays = C4.devsOf('relay'), ls = C4.devsOf('l_sensor');
      return head(t('cat_security')) + body((sensors.length || ls.length ? `${relays.length ? `<div class="secth">${t('locks_sensors')}</div>` : ''}<div class="cards">${sensors.map(secCard).join('')}${ls.map(lCard).join('')}</div>` : '')
        + (relays.length ? `<div class="secth">${t('relays')}</div><div class="cards">${relays.map(swCard).join('')}</div>` : ''));
    }
    if (k === 'camera') return head(t('cat_camera')) + body(`<div class="camg">${C4.camList().map(d => `<div class="camc">${C4.camHtml(d)}
        <span class="lbl">${S.cams[d.id] && S.cams[d.id].mode === 'rtsp' ? `<span class="live">${t('live')}</span>` : ''} ${esc(C4.label(d))}${d.other ? ` · ${esc(d.room)}` : ''}</span></div>`).join('')}</div>`);
    return body(`<p class="empty">${t('no_devices')}</p>`);
  }
  // Angelerntes Gerät: Zustand + bis zu 2 Infos auf der Karte, alle Infos im Tooltip; Knöpfe je nach Profil-Aktionen
  const lCards = type => C4.devsOf(type).map(lCard).join('');
  function lState(d, L) {
    const s = L.state;
    if (s == null || s === '') return L.known === false || !Object.keys(L.values || {}).length ? '' : t('l_unknown');
    if (d.ltype === 'sensor') return s === 'on' ? t('open') : s === 'off' ? t('closed') : String(s);
    return { on: t('on'), off: t('off'), play: t('l_play'), pause: t('l_pause'), stop: t('l_stop') }[s] ?? String(s);
  }
  function lCard(d) {
    const L = C4.learned(d), a = L.actions || [], lt = d.ltype;
    const icon = { sensor: L.active ? 'window_open' : 'window_closed', switch: 'power', light: L.active ? 'light_on' : 'light_off',
      blind: 'blind_open', media: 'music', thermostat: 'thermostat', info: 'info' }[lt] || 'gear';
    const infos = L.infos || [], warn = infos.some(i => i.warn);
    const sens = lt === 'sensor';               // Sensor wie die übrigen Kontakte: Zustand rechts farbig
    const sub = [sens ? '' : lState(d, L), ...infos.filter(i => i.place === 'card').slice(0, 2).map(i => (i.warn ? '⚠ ' : '') + C4.infoText(i))].filter(Boolean).join(' · ')
      || (sens ? d.name : '');
    const tip = [d.name, ...infos.map(i => `${i.label}: ${C4.infoText(i)}`), `(${t('l_learned')})`].join('\n');
    const act = (x, label, v) => `<button onclick="C4.learnedAct(${d.id},'${x}'${v != null ? ',' + v : ''})">${label}</button>`;
    let btn = '';
    if (lt === 'blind') btn = `<span class="bb">${a.includes('open') ? act('open', ic('chev_up', 18)) : ''}${a.includes('stop') ? act('stop', ic('stop', 16)) : ''}${a.includes('close') ? act('close', ic('chev_down', 18)) : ''}</span>`;
    else if (lt === 'media') btn = `<span class="bb">${a.includes('prev') ? act('prev', ic('prev', 16)) : ''}${a.includes(L.state === 'play' ? 'pause' : 'play') ? act(L.state === 'play' ? 'pause' : 'play', ic(L.state === 'play' ? 'pause' : 'play', 16)) : ''}${a.includes('next') ? act('next', ic('next', 16)) : ''}</span>`;
    else if (a.includes('on') && a.includes('off') && L.active != null) btn = `<button class="sw ${L.active ? 'on' : ''}" onclick="C4.learnedAct(${d.id},'${L.active ? 'off' : 'on'}')"></button>`;
    else if (a.includes('toggle')) btn = `<button class="sw ${L.active ? 'on' : ''}" onclick="C4.learnedAct(${d.id},'toggle')"></button>`;
    else if (a.includes('on') || a.includes('off')) btn = `<span class="bb">${a.includes('on') ? act('on', t('on')) : ''}${a.includes('off') ? act('off', t('off')) : ''}</span>`;
    const cls = lt === 'sensor' ? (L.active == null ? '' : L.active ? 'alert' : 'okc') : (L.active ? 'on' : '') + (warn ? ' warnc' : '');
    return `<div class="c ${cls}" title="${esc(tip)}"><span class="ci">${ic(icon, 26)}</span>
      <span class="cx"><b>${esc(C4.label(d))}</b><small>${esc(sub)}</small></span>${btn}
      ${sens ? `<span class="st" style="color:${L.active == null ? 'var(--dim)' : L.active ? 'var(--err)' : 'var(--ok)'}">${esc(lState(d, L) || t('l_unknown'))}</span>` : favBtn(d.id)}</div>`;
  }
  function lightCard(d, l, other) {
    const lvl = l.on ? 0 : 100;
    return `<div class="c ${l.on ? 'on' : ''}"><span class="ci" onclick="X.lt(${d.id},${lvl})">${ic(l.on ? 'light_on' : 'light_off', 26)}</span>
      <span class="cx" onclick="X.lt(${d.id},${lvl})"><b>${esc(d.note || d.name)}</b><small>${l.on ? (l.dimmable && l.level != null ? l.level + ' %' : t('on')) : t('off')}</small></span>
      <button class="sw ${l.on ? 'on' : ''}" onclick="X.lt(${d.id},${lvl})"></button>
      ${l.dimmable ? rng(l.level, `data-lvl="${d.id}"`) : ''}${other ? '' : favBtn(d.id)}</div>`;
  }
  function blindCard(d) {
    const b = C4.blind(d);
    return `<div class="c ${b.moving ? 'on' : ''}"><span class="ci" onclick="C4.blindToggle(${d.id})">${ic(b.open ? 'blind_open' : 'blind_closed', 26)}</span>
      <span class="cx" onclick="C4.blindToggle(${d.id})"><b>${esc(C4.label(d))}</b><small>${blindTxt(b)}${b.group ? ' · ' + t('group') : ''}</small></span>
      <span class="bb"><button title="↑" onclick="C4.blindDo(${d.id},'open')">${ic('chev_up', 18)}</button>
        <button title="■" onclick="C4.blindDo(${d.id},'stop')">${ic('stop', 16)}</button>
        <button title="↓" onclick="C4.blindDo(${d.id},'close')">${ic('chev_down', 18)}</button></span>
      ${b.hasLevel ? rng(b.level, `data-blind="${d.id}"`) : ''}${favBtn(d.id)}</div>`;
  }
  function swCard(d) {
    const s = C4.sw(d);
    return `<div class="c ${s.on ? 'on' : ''}"><span class="ci" onclick="C4.switchToggle(${d.id})">${ic('power', 26)}</span>
      <span class="cx" onclick="C4.switchToggle(${d.id})"><b>${esc(C4.label(d))}</b><small>${s.known ? (s.on ? t('on') : t('off')) : t('sw_unknown')}</small></span>
      ${s.known ? `<button class="sw ${s.on ? 'on' : ''}" onclick="C4.switchToggle(${d.id})"></button>`
        : `<span class="bb"><button onclick="C4.switchSet(${d.id},true)">${t('on')}</button><button onclick="C4.switchSet(${d.id},false)">${t('off')}</button></span>`}
      ${favBtn(d.id)}</div>`;
  }
  function secCard(d) {
    const s = C4.sec(d);
    return `<div class="c ${!s.known ? '' : s.active ? 'alert' : 'okc'}"><span class="ci">${ic(s.icon, 26)}</span>
      <span class="cx"><b>${esc(C4.label(d))}</b><small>${esc(d.name)}</small></span>
      <span class="st" style="color:${!s.known ? 'var(--dim)' : s.active ? 'var(--err)' : 'var(--ok)'}">${esc(s.text)}</span></div>`;
  }
  function climHtml(d) {
    const c = C4.clim(d);
    const frac = c.temp == null ? 0 : Math.max(0, Math.min(1, (c.temp - 5) / 25));
    const modeIcon = m => ({ Heat: 'flame', Cool: 'snow', Off: 'power' }[m] || 'refresh');
    return `<div class="secth">${esc(C4.label(d))}</div><div class="clim">
      <div class="cside">${c.modes.map(m => `<button class="cbtn ${c.mode.toLowerCase() === m.toLowerCase() ? 'on' : ''}" onclick="C4.climMode(${d.id},'${jsq(m)}')">${ic(modeIcon(m), 18)} ${esc(modeLbl(m))}</button>`).join('')}</div>
      <div class="ring"><svg viewBox="0 0 300 300" width="300" height="300">
          <defs><linearGradient id="cg${d.id}"><stop offset="0" stop-color="#ffcf8a"/><stop offset="1" stop-color="#ff7b45"/></linearGradient></defs>
          <circle cx="150" cy="150" r="135" fill="none" stroke="rgba(255,255,255,.1)" stroke-width="12" stroke-dasharray="636 848" transform="rotate(135 150 150)" stroke-linecap="round"/>
          <circle cx="150" cy="150" r="135" fill="none" stroke="${c.heating ? `url(#cg${d.id})` : '#7ec855'}" stroke-width="12" stroke-dasharray="${(636 * frac).toFixed(1)} 848" transform="rotate(135 150 150)" stroke-linecap="round"/></svg>
        <div class="val"><small>${t('actual')}</small><b>${c.temp != null ? C4.num1(c.temp) + '°' : '–'}</b>
          ${c.sp != null ? `<span class="sp">${ic('flame', 14)} ${c.heating ? t('heating') + ' · ' : ''}${t('setpoint')} ${C4.num1(c.sp)}°</span>` : `<small>${t('wait_reading')}</small>`}
          ${c.humidity != null ? `<small style="margin-top:8px">${t('humidity')} ${Math.round(c.humidity)} %</small>` : ''}</div></div>
      <div class="cside"><span class="hint">${t('setpoint')}</span>
        <div class="stepper"><button onclick="C4.climStep(${d.id},-0.5)">−</button><b>${c.sp != null ? C4.num1(c.sp) + '°' : '–'}</b><button onclick="C4.climStep(${d.id},0.5)">+</button></div>
        ${c.hold ? `<span class="hint">${t('hold')}: ${esc(c.hold)}</span>` : ''}
        <button class="btn" onclick="C4.toggleFav(${d.id})">${ic('star', 16)} ${C4.isFav(d.id) ? t('on_home') : t('add_fav')}</button></div></div>`;
  }

  // ---- Hören: Musik-Browser ----
  function musicHtml() {
    return `<div class="mb"><div class="side" id="mbside"></div><div class="main" id="mbmain"></div></div>`;
  }
  function updateSources() {
    const side = $('#mbside'); if (!side) return;
    const np = C4.nowPlaying();
    const lib = [['album', 'grid', t('albums')], ['artist', 'user', t('artists')], ['genre', 'folder_music', t('genres')],
      ['song', 'note', t('tab_songs')], ['recent', 'clock', t('recent')], ['random', 'shuffle', t('random')]];
    const libOn = m => (m === 'album' ? ['album', 'detail', 'search'].includes(mb.mode) && !(mb.mode === 'detail' && mbPrev && mbPrev.mode !== 'album') : mb.mode === m
      || (mb.mode === 'detail' && mbPrev && mbPrev.mode === m));
    side.innerHTML = `<div class="secth">${t('library')}</div>` + lib.map(([m, i, l]) =>
      `<button class="s ${libOn(m) ? 'on' : ''}" onclick="X.mbSet({mode:'${m}',artist:null,genre:null,q:''})">${ic(i, 16)} ${l}</button>`).join('')
      + (C4.isBluos() ? bluSide(np) : `<div class="secth">${t('sources')}</div>` + (S.media.listen || []).map(s =>
        `<button class="s ${S.rs.selected_device === s.id && np.on ? 'live' : ''}" onclick="C4.selectSource(${s.id},'listen')">${C4.srcIcon(s, 'music', 18)} ${esc(s.name)}</button>`).join(''));
  }
  // BluOS-Raum: Dienste (Qobuz, Radio …) und Eingänge (Line-In …) des Players statt Control4-Quellen
  let bluSideHtml = '';
  function bluSide(np) {
    if (!S.bluTop) { C4.bluTop().then(() => updateSources()); return `<div class="secth">${t('services')}</div><p class="hint" style="padding:0 12px">…</p>`; }
    const b = S.rs.bluos || {};
    const svc = S.bluTop.map((it, i) => [it, i]).filter(([it]) => it.browse && !/^LocalMusic:/.test(it.browse));
    const inp = S.bluTop.map((it, i) => [it, i]).filter(([it]) => it.input && it.play);
    bluSideHtml = `<div class="secth">${t('services')}</div>` + svc.map(([it, i]) =>
        `<button class="s ${mb.mode === 'blu' && mb.blu === i ? 'on' : ''} ${b.service && it.browse.startsWith(b.service + ':') && np.on ? 'live' : ''}" onclick="X.bluGo(${i})">${ic('folder_music', 16)} ${esc(it.text)}</button>`).join('')
      + (inp.length ? `<div class="secth">${t('inputs')}</div>` + inp.map(([it, i]) =>
        `<button class="s ${b.service === 'Capture' && b.title === it.text && np.on ? 'live' : ''}" onclick="X.bluInput(${i})">${ic('power', 16)} ${esc(it.text)}</button>`).join('') : '');
    return bluSideHtml;
  }
  function bluGo(i) {
    const it = S.bluTop[i]; mbPrev = null;
    mb = { ...mb, mode: 'blu', blu: i, q: '' };
    updateSources();
    const main = $('#mbmain'); main.innerHTML = `<div id="bluv"></div>`;
    C4.bluStart($('#bluv'), it.browse, it.text);
    const v = $('#view'); if (v) v.scrollTop = 0;
  }
  function bluInput(i) { C4.bluPlay(S.bluTop[i].play); }
  // Control4-Quelle wählen; im BluOS-Raum (TuneIn, Qobuz, Unsere Musik …) zusätzlich den Dienst im Player-Browser öffnen
  async function srcGo(id, kind) {
    C4.selectSource(id, kind);
    const key = C4.bluKey(id);
    if (!key) return;
    if (key === 'LocalMusic:') { mb = { ...mb, mode: 'album', artist: null, genre: null, q: '' }; mbPrev = null; return go('cat:listen'); }
    const i = (await C4.bluTop()).findIndex(x => x.browse === key);
    if (i < 0) return;
    mb = { ...mb, mode: 'blu', blu: i, q: '' }; mbPrev = null;
    go('cat:listen');
  }
  const albGrid = list => `<div class="mgrid ${C4.albView() === 'list' ? 'list' : ''}">${list.map(a => `<div class="alb" onclick="X.openAlbum(${a.id})">${C4.coverHtml(a.artist, a.title, 'cover', C4.coverUrl(a.img))}
      <b>${esc(a.title)}</b><small>${esc(a.artist)}${a.year ? ' · ' + a.year : ''}</small></div>`).join('')}</div>`;
  const songRow = (s, i) => `<div class="trk srow" title="${C4.canPlay() ? esc(t('play_song')) : ''}" onclick="${C4.canPlay() ? `C4.playSong(${s.b},0,${s.i})` : `X.openAlbum(${s.b},${s.i})`}"><span class="n">${ic('note', 15)}</span>
      <span class="tt"><b>${esc(s.n)}</b><small>${esc(s.a)}${s.t ? ' · ' + esc(s.t) : ''}</small></span>
      <button class="pf" title="${t('open_album')}" onclick="event.stopPropagation();X.openAlbum(${s.b},${s.i})">${ic('folder_music', 14)}</button>
      ${C4.canPlay() ? `<button class="pf" title="${t('play_from')}" onclick="event.stopPropagation();C4.playAlbum(${s.b},0,${s.i})">${ic('play', 14)} ${t('play_from')}</button>` : ''}</div>`;
  const noPlayNote = () => (C4.canPlay() ? '' : `<p class="note">${ic('info', 16)} ${t('not_c4room')}</p>`);

  // Titel A–Z: 4000+ Zeilen → in Häppchen rendern, Sprungleiste lädt bis zum Buchstaben nach
  let songs = null, songsShown = 0, songObs = null;
  function renderSongs(upto) {
    const box = $('#songlist'); if (!box || !songs) return;
    const end = Math.min(songs.length, Math.max(upto || 0, songsShown + 200));
    if (end > songsShown) {
      box.insertAdjacentHTML('beforeend', songs.slice(songsShown, end).map((s, k) => {
        const i = songsShown + k, L = C4.letterOf(s.n);
        const head = !i || C4.letterOf(songs[i - 1].n) !== L ? `<div class="lh" id="L-${L}">${L}</div>` : '';
        return head + songRow(s);
      }).join(''));
      songsShown = end;
    }
    const sen = $('#songsen'); if (sen) sen.hidden = songsShown >= songs.length;
  }
  function songJump(L) {
    const idx = songs.findIndex(s => C4.letterOf(s.n) === L); if (idx < 0) return;
    renderSongs(idx + 60);
    const el = document.getElementById('L-' + L); if (el) el.scrollIntoView({ block: 'start' });
  }

  async function renderMusic(force) {
    updateSources();
    const main = $('#mbmain'); if (!main) return;
    if (mb.mode === 'blu') { if (!main.querySelector('#bluv')) bluGo(mb.blu); return; }
    const seq = ++mbSeq;
    const titles = { artist: t('artists'), genre: t('genres'), song: t('tab_songs'), recent: t('recent'), random: t('random') };
    const title = titles[mb.mode] || mb.artist || mb.genre || t('albums');
    const searchBox = `<label class="search">${ic('search', 18)}<input id="mq" placeholder="${t('search_music')}" value="${esc(mb.q)}" autocomplete="off"></label>`;
    const keepFocus = document.activeElement && document.activeElement.id === 'mq';
    if (force && !main.querySelector('#mq')) main.innerHTML = `<div class="mh"><h2>${esc(title)}</h2>${searchBox}</div><div id="mbc"><p class="empty">…</p></div>`;
    const setHead = (h, cnt, extra = '') => {
      const mh = main.querySelector('.mh');
      mh.innerHTML = `<h2>${esc(h)}</h2>${cnt != null ? `<span class="cnt">${cnt}</span>` : ''}${extra}`;
      mh.appendChild(main.querySelector('.search') || Object.assign(document.createElement('div'), { innerHTML: searchBox }).firstChild);
    };
    const mbc = () => $('#mbc');
    try {
      if (mb.mode === 'detail') {
        const a = await C4.album(mb.albumId); if (seq !== mbSeq) return;
        setHead(t('albums'), null, `<button class="btn" onclick="X.mbBack()">${ic('chev_left', 16)} ${t('back')}</button>`);
        const play = C4.canPlay();
        mbc().innerHTML = `<div class="adet"><div>${C4.coverHtml(a.artist, a.title, 'cover', C4.coverUrl(a.img))}<h2>${esc(a.title)}</h2>
            <p>${esc(a.artist)}${a.year ? ' · ' + a.year : ''} · ${t('n_songs', a.songs.length)}</p>
            ${play ? `<button class="btn pri" onclick="C4.playAlbum(${a.id})">${ic('play', 16)} ${t('play_album')}</button>
            <p class="hint" style="margin-top:10px">${t('playing_in', esc(S.room.name))}</p>` : noPlayNote()}</div>
          <div>${a.songs.map((s, i) => `<div class="trk ${s.id === mb.trackId ? 'hit' : ''} ${play ? 'srow' : ''}" id="trk-${s.id}" ${play ? `onclick="C4.playSong(${a.id},${i + 1},${s.id})"` : ''}><span class="n">${s.track_no || i + 1}</span>
              <span class="tt">${esc(s.name)}${s.artist && s.artist !== a.artist ? `<small>${esc(s.artist)}</small>` : ''}</span>
              ${play ? `<button class="pf" title="${t('play_from')}" onclick="event.stopPropagation();C4.playAlbum(${a.id},${i + 1},${s.id})">${ic('play', 14)} ${t('play_from')}</button>` : ''}</div>`).join('')}
            ${play ? `<p class="hint" style="margin:12px 12px 0">${t('track_hint')}</p>` : ''}</div></div>`;
        if (mb.trackId) { const el = document.getElementById('trk-' + mb.trackId); if (el) el.scrollIntoView({ block: 'center' }); }
      } else if (mb.mode === 'search') {
        const r = await C4.search(mb.q); if (seq !== mbSeq) return;
        const n = r.artists.length + r.albums.length + r.songs.length;
        setHead(t('search_res', mb.q), n);
        let h = '';
        if (r.artists.length) h += `<div class="secth">${t('sec_artists')}</div>` + r.artists.map(x =>
          `<div class="lrow" onclick="X.mbSet({mode:'album',artist:'${jsq(x.artist)}'})"><span class="av" style="${C4.coverStyle(x.artist, '')}">${esc(x.artist.split(/\s+/).map(w => w[0]).slice(0, 2).join(''))}</span>
            <b>${esc(x.artist)}</b><small>${t('n_albums', x.albums)}</small></div>`).join('');
        if (r.albums.length) h += `<div class="secth">${t('sec_albums')}</div>` + albGrid(r.albums);
        if (r.songs.length) h += `<div class="secth">${t('sec_songs')}</div>` + r.songs.map(s => songRow({ i: s.id, n: s.name, a: s.artist, b: s.album_id, t: s.album, k: s.track_no })).join('');
        mbc().innerHTML = h || `<p class="empty">${t('no_hits')}</p>`;
      } else if (mb.mode === 'artist' || mb.mode === 'genre') {
        const r = await C4.browse(mb.mode); if (seq !== mbSeq) return;
        setHead(title, r.length);
        mbc().innerHTML = r.map(x => {
          const n = mb.mode === 'artist' ? x.artist : x.genre;
          return `<div class="lrow" onclick="X.mbSet({mode:'album',${mb.mode}:'${jsq(n)}'})"><span class="av" style="${C4.coverStyle(n, '')}">${esc(n.split(/\s+/).map(w => w[0]).slice(0, 2).join(''))}</span>
            <b>${esc(n)}</b><small>${t('n_albums', x.albums)}</small></div>`;
        }).join('');
      } else if (mb.mode === 'song') {
        songs = await C4.browse('song'); if (seq !== mbSeq) return;
        setHead(title, songs.length);
        const letters = [...new Set(songs.map(s => C4.letterOf(s.n)))];
        mbc().innerHTML = `${noPlayNote()}<div class="az">${letters.map(L => `<button onclick="X.songJump('${L}')">${L}</button>`).join('')}</div>
          <div id="songlist"></div><div id="songsen" class="empty">…</div>`;
        songsShown = 0; renderSongs(200);
        if (songObs) songObs.disconnect();
        songObs = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) renderSongs(); }, { root: $('#view'), rootMargin: '600px' });
        songObs.observe($('#songsen'));
      } else if (mb.mode === 'recent') {
        const r = await C4.recent(); if (seq !== mbSeq) return;
        setHead(title, r.length, C4.albViewBtn('X.mbRe()'));
        mbc().innerHTML = noPlayNote() + (r.length ? albGrid(r) : `<p class="empty">${t('no_recent')}</p>`);
      } else if (mb.mode === 'random') {
        const r = await C4.randomAlbums(); if (seq !== mbSeq) return;
        setHead(title, null, `<button class="btn" onclick="X.mbSet({mode:'random'})">${ic('shuffle', 16)} ${t('reshuffle')}</button>` + C4.albViewBtn('X.mbRe()'));
        mbc().innerHTML = noPlayNote() + albGrid(r);
      } else {
        const r = await C4.browse('album', mb.artist, mb.genre); if (seq !== mbSeq) return;
        setHead(title, r.length, (mb.artist || mb.genre ? `<button class="btn" onclick="X.mbSet({mode:'album',artist:null,genre:null})">${ic('x', 14)}</button>` : '') + C4.albViewBtn('X.mbRe()'));
        mbc().innerHTML = noPlayNote() + albGrid(r);
      }
    } catch (e) { if (seq === mbSeq) $('#mbc').innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
    if (keepFocus) { const q = $('#mq'); if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } }
  }
  let mbPrev = null;
  function mbSet(p) { mb = { ...mb, ...p, trackId: null }; if (p.mode && p.mode !== 'search') mb.q = p.q ?? ''; mbPrev = null; renderMusic(true); const v = $('#view'); if (v) v.scrollTop = 0; }
  function openAlbum(id, trackId) { if (mb.mode !== 'detail') mbPrev = { ...mb }; mb = { ...mb, mode: 'detail', albumId: id, trackId: trackId || null }; renderMusic(true); const v = $('#view'); if (v && !trackId) v.scrollTop = 0; }
  function mbRe() { renderMusic(true); }
  function mbBack() { mb = mbPrev || { mode: 'album', artist: null, genre: null, q: '' }; mbPrev = null; renderMusic(true); }

  // ---- Now Playing ----
  let q = { key: '', data: null };
  async function loadQueue() {
    const np = C4.nowPlaying();
    const key = [S.room.id, np.album, np.title, S.rs.queue_index, (S.rs.bluos || {}).song].join('|');
    if (key === q.key) return;
    q.key = key;
    try { q.data = await C4.queue(); } catch { q.data = null; }
    const el = $('#qbox'); if (el) el.innerHTML = queueHtml();
    const sq = $('#npq'); if (sq) sq.innerHTML = queueShort();
  }
  // Kurzform für die Leiste: aktueller Titel + die nächsten
  function queueShort() {
    const d = q.data, np = C4.nowPlaying();
    if (!d || !d.songs || !d.songs.length) return `<p class="empty">${t('queue_empty')}</p>`;
    const blu = d.backend === 'bluos';
    let cur = blu ? d.index : d.songs.findIndex(s => s.name === np.title);
    if (cur < 0) cur = 0;
    const jump = (s, i) => (blu ? `C4.queueJump(${s.pos})` : `C4.playAlbum(${d.album && d.album.id},${i + 1},${s.id || 0})`);
    const can = blu || (d.album && C4.canPlay());
    setTimeout(() => {      // aktuellen Titel in den sichtbaren Bereich der Liste holen
      const box = $('#npq'), hit = box && box.querySelector('.qs.hit');
      if (hit) box.scrollTop = Math.max(0, hit.offsetTop - box.offsetTop - 8);
    }, 0);
    return d.songs.map((s, i) => `<div class="qs ${i === cur ? 'hit' : ''}${can && i !== cur ? ' j' : ''}"${can && i !== cur ? ` onclick="${jump(s, i)}"` : ''}>
      <span class="n">${i === cur ? ic('volume', 13) : (s.track_no || i + 1)}</span><span class="tt">${esc(s.name)}</span>
      <small>${esc(blu ? (s.artist || '') : (np.artist || ''))}</small></div>`).join('');
  }
  function queueHtml() {
    const d = q.data, np = C4.nowPlaying();
    if (!d || !d.songs || !d.songs.length) return `<p class="empty">${t('queue_empty')}</p>`;
    const blu = d.backend === 'bluos';
    const cur = blu ? d.index : d.songs.findIndex(s => s.name === np.title);
    const jump = (s, i) => (blu ? `C4.queueJump(${s.pos})` : `C4.playAlbum(${d.album && d.album.id},${i + 1},${s.id || 0})`);
    return d.songs.map((s, i) => `<div class="trk ${i === cur ? 'hit' : ''}"><span class="n">${i === cur ? ic('volume', 14) : (s.track_no || i + 1)}</span>
      <span class="tt">${esc(s.name)}${blu && s.artist && s.album !== (d.songs[0] || {}).album ? `<small>${esc(s.artist)} · ${esc(s.album)}</small>` : ''}</span>
      ${(blu || (d.album && C4.canPlay())) && i !== cur ? `<button class="pf" title="${t('play_from')}" onclick="${jump(s, i)}">${ic('play', 14)}</button>` : ''}</div>`).join('');
  }
  function npHtml() {
    const np = C4.nowPlaying();
    if (!np.on) return `<div class="sheet"><div class="sh-b"><p class="empty">${t('nothing_active')}</p>
      <button class="btn" onclick="X.go('cat:listen')">${ic('music', 16)} ${t('listen_start')}</button></div></div>`;
    if (np.watch) return watchHtml();
    const art = C4.coverHtml(np.artist, np.album || np.title || np.source, 'cover', np.img);
    const srcs = [...(S.media.listen || []), ...(S.media.watch || [])];
    setTimeout(loadQueue, 0);
    return `<div class="npv"><div class="art">${art}
        <h2>${esc(np.title || np.source || t('no_source'))}</h2><p>${esc([np.artist, np.album, np.title ? np.source : t('line_in'), S.room.name].filter(Boolean).join(' · '))}</p>
        <div class="ctrl">${C4.mixButton(18)}<button onclick="C4.transport('SKIP_REV')">${ic('prev', 22)}</button>${C4.ppButtons('pp', 26)}
          <button onclick="C4.transport('SKIP_FWD')">${ic('next', 22)}</button>${C4.repeatButton(18)}</div>
        <div class="volrow"><button onclick="C4.mute(${!np.muted})">${ic(np.muted ? 'vol_mute' : 'volume', 22)}</button>
          <button onclick="C4.volumeStep(-3)">−</button>${rng(np.volume, 'data-vol="1"')}<button onclick="C4.volumeStep(3)">+</button><span>${np.volume}</span></div>
        <p style="margin-top:16px"><button class="btn" style="color:#ff8b98" onclick="C4.roomOff()">${ic('power', 16)} ${t('room_off')}</button></p></div>
      <div><div class="sheet" style="min-height:0;margin-bottom:16px"><div class="sh-h"><h2>${t('queue')}</h2></div><div class="sh-b" id="qbox">${queueHtml()}</div></div>
      <div class="sheet" style="min-height:0"><div class="sh-h"><h2>${t('sources')}</h2></div><div class="sh-b"><div class="cards">${srcs.map(s =>
        `<div class="c ${S.rs.selected_device === s.id ? 'on' : ''} ${C4.bluDead(s.id) ? 'dis' : ''}" ${C4.bluDead(s.id) ? `title="${esc(t('blu_dead'))}"` : ''} onclick="X.srcGo(${s.id},'${(S.media.watch || []).includes(s) ? 'watch' : 'listen'}')">
          <span class="ci">${C4.srcIcon(s, (S.media.watch || []).includes(s) ? 'tv' : 'music', 26)}</span><span class="cx"><b>${esc(s.name)}</b></span></div>`).join('')}</div></div></div></div></div>`;
  }

  let npKey = '';
  function refreshNp() {
    const np = C4.nowPlaying(), key = [np.on, np.watch, np.title, np.album, np.source, np.img].join('|');
    if (key !== npKey) { npKey = key; $('#view').innerHTML = npHtml(); return; }
    const pp = $('.npv .ctrl'); if (pp) pp.innerHTML = `${C4.mixButton(18)}<button onclick="C4.transport('SKIP_REV')">${ic('prev', 22)}</button>${C4.ppButtons('pp', 26)}
          <button onclick="C4.transport('SKIP_FWD')">${ic('next', 22)}</button>${C4.repeatButton(18)}`;
    const vr = $('.npv .volrow');
    if (vr && !dragging) vr.innerHTML = `<button onclick="C4.mute(${!np.muted})">${ic(np.muted ? 'vol_mute' : 'volume', 22)}</button>
          <button onclick="C4.volumeStep(-3)">−</button>${rng(np.volume, 'data-vol="1"')}<button onclick="C4.volumeStep(3)">+</button><span>${np.volume}</span>`;
    loadQueue();
  }
  // Unterer Bereich (PC): groß Cover + Kurz-Warteschlange, darunter die Leiste, darunter optional das Wetter
  const big = () => innerWidth >= 1000 && innerHeight >= 820;
  const wide = () => innerWidth >= 1200;     // PC / großes Tablet quer: Musik + Wetter als rechte Seitenleiste
  const xwide = () => innerWidth >= 1800;    // Vollbild am PC: Player und Wetter als zwei Spalten nebeneinander
  // Kompakt (PC, aber schmaler als 1800 px oder niedriger als 1050 px): rechts nur der Player, Wetter als Band unten
  const cmp = () => wide() && (innerWidth < 1800 || innerHeight < 1050);
  const bandHours = () => innerWidth >= 1600 ? 10 : innerWidth >= 1400 ? 8 : 6;
  const wxOn = () => { try { return localStorage.getItem('c4wx') !== '0'; } catch { return true; } };
  function toggleWx() { try { localStorage.setItem('c4wx', wxOn() ? '0' : '1'); } catch { } renderNp(); renderMenu(); if (wxOn()) loadWx(true); }
  let npSig = '';
  function renderNp() {
    const np = C4.nowPlaying(), el = $('#np');
    const bar = np.on && view !== 'np' && !(np.watch && view === 'cat:watch');
    const ext = bar && !np.watch && (wide() || big());
    const c = cmp(), band = wxOn() && c && view !== 'np';
    const wx = wxOn() && (wide() || big()) && view !== 'np' && !c;
    el.hidden = !bar && !wx;
    const fr = document.querySelector('.x4');
    if (fr) { fr.classList.toggle('side', wide() && !el.hidden); fr.classList.toggle('side2', xwide() && !c && !el.hidden && wx && bar); fr.classList.toggle('cmp', c); }
    const wb = $('#wxb');
    if (wb) {
      wb.hidden = !band;
      if (band && wb.dataset.n !== String(bandHours())) { wb.dataset.n = bandHours(); wb.innerHTML = wxHtml(bandHours()); }
      if (band) loadWx();
    }
    if (el.hidden || (dragging && el.contains(document.activeElement))) return;
    // Cover/Warteschlange/Wetter nur neu bauen, wenn sich etwas geändert hat (sonst flackert das Bild)
    const sig = [bar, ext, wx, xwide(), c, np.img, np.title, np.album, np.artist, S.room.id].join('|');
    if (sig !== npSig || !$('#npbar')) {
      npSig = sig;
      el.innerHTML = (ext ? `<div class="npx"><div class="npc" onclick="X.go('np')">${C4.coverHtml(np.artist, np.album || np.title || np.source, 'cover', np.img)}</div>
          <div class="npq" id="npq">${queueShort()}</div></div>` : '')
        + `<div class="bar" id="npbar"${bar ? '' : ' hidden'}></div>`
        + (wx ? `<div class="npw" id="npw">${wxHtml()}</div>` : '');
      if (ext) setTimeout(loadQueue, 0);
      if (wx) loadWx();
    }
    if (bar) renderBar(np);
  }
  // Liedzeit (BluOS liefert secs/total; dazwischen rechnet die Oberfläche selbst weiter)
  const mmss = s => (s >= 3600 ? Math.floor(s / 3600) + ':' + String(Math.floor(s % 3600 / 60)).padStart(2, '0') : Math.floor(s / 60)) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  function songPos(np) {
    if (!np.total) return null;
    const run = np.playState === 'play' && np.at ? Math.max(0, Date.now() / 1000 - np.at) : 0;
    return Math.min(np.total, np.secs + run);
  }
  function progHtml(np) {
    const p = songPos(np);
    if (p == null) return '';
    return `<div class="prog"><span>${mmss(p)}</span><i><b style="width:${(100 * p / np.total).toFixed(1)}%"></b></i><span>${mmss(np.total)}</span></div>`;
  }
  setInterval(() => {
    const els = document.querySelectorAll('#npbar .prog, .knp .prog'); if (!els.length) return;
    const np = C4.nowPlaying(), p = songPos(np); if (p == null) return;
    els.forEach(el => { el.children[0].textContent = mmss(p); el.querySelector('b').style.width = (100 * p / np.total).toFixed(1) + '%'; });
  }, 1000);
  function renderBar(np) {
    const el = $('#npbar'); if (!el) return;
    const bigCover = !!$('#np .npx');
    el.classList.toggle('nocov', bigCover);
    el.innerHTML = (bigCover ? '' : `<div class="nc" onclick="X.go('np')">${np.img ? `<img src="${esc(np.img)}" onerror="this.remove()">` : ic(np.watch ? 'tv' : 'music', 24)}</div>`) + `
      <div class="nt" onclick="X.go('np')"><b>${esc(np.title || np.source || t('no_source'))}</b><small>${esc(S.room.name)}${np.title && np.source ? ' · ' + esc(np.source) : ''}</small>${bigCover ? progHtml(np) : ''}</div>
      <div class="tr">${np.watch ? `<button class="x" onclick="X.go('cat:watch')" title="${t('remote')}">${ic('remote', 22)}</button>` : ''}${np.watch && !np.kodi ? '' : C4.mixButton(15)}<button class="x" onclick="C4.transport('SKIP_REV')">${ic('prev', 20)}</button>${C4.ppButtons('pp', 18)}
        <button class="x" onclick="C4.transport('SKIP_FWD')">${ic('next', 20)}</button>${np.watch && !np.kodi ? '' : C4.repeatButton(15)}</div>
      <div class="vol"><button onclick="C4.mute(${!np.muted})">${ic(np.muted ? 'vol_mute' : 'volume', 20)}</button>${rng(np.volume, 'data-vol="1"')}<span>${np.volume}</span></div>
      <button class="pw" title="${t('room_off')}" onclick="C4.roomOff()">${ic('power', 22)}</button>`;
  }

  // ---- Wetter (jetzt · nächste Stunden · morgen), alle 10 min ----
  let wxData = null, wxErr = '', wxAt = 0;
  async function loadWx(force) {
    if (!force && Date.now() - wxAt < 600000) return;
    wxAt = Date.now();
    try { wxData = await C4.api('/api/weather/panel?hours=12'); wxErr = ''; } catch (e) { wxData = null; wxErr = e.message; }
    const el = $('#npw'); if (el) el.innerHTML = wxHtml();
    const wb = $('#wxb'); if (wb && !wb.hidden) wb.innerHTML = wxHtml(bandHours());
  }
  const WXI = c => c === 0 ? '☀️' : c <= 2 ? '🌤️' : c === 3 ? '☁️' : c <= 48 ? '🌫️' : c <= 67 || (c >= 80 && c <= 82) ? '🌧️' : c <= 86 ? '🌨️' : '⛈️';
  const wi = (c, day) => (day === 0 && c <= 2) ? '🌙' : WXI(c);
  const deg = v => v == null ? '–' : Math.round(v) + '°';
  // Wettertext: der Server liefert Deutsch (die Sprachausgabe liest ihn vor) – auf Englisch aus dem WMO-Code
  const WX_EN = { 0: 'clear', 1: 'mostly clear', 2: 'partly cloudy', 3: 'overcast', 45: 'fog', 48: 'freezing fog', 51: 'light drizzle',
    53: 'drizzle', 55: 'heavy drizzle', 56: 'freezing drizzle', 57: 'freezing drizzle', 61: 'light rain', 63: 'rain', 65: 'heavy rain',
    66: 'freezing rain', 67: 'freezing rain', 71: 'light snow', 73: 'snow', 75: 'heavy snow', 77: 'snow grains', 80: 'rain showers',
    81: 'heavy rain showers', 82: 'violent rain showers', 85: 'snow showers', 86: 'heavy snow showers', 95: 'thunderstorm',
    96: 'thunderstorm with hail', 99: 'thunderstorm with hail' };
  const cond = o => (document.documentElement.lang === 'en' && WX_EN[o.code]) || o.condition || '';
  function wxHtml(nh = xwide() ? 12 : 6) {
    if (!wxData) return `<p class="empty">${wxErr ? esc(wxErr) : '…'}</p>`;
    const n = wxData.now || {}, m = wxData.tomorrow || {};
    return `<div class="wc now"><span class="wi">${wi(n.code, n.day)}</span><div><b>${deg(n.temp)}</b><small>${esc(cond(n))}</small>
        <small>${t('wx_feels')} ${deg(n.feels)} · ${deg(n.t_min)} / ${deg(n.t_max)}</small></div></div>
      <div class="wc hrs">${(wxData.hours || []).slice(0, nh).map(h => `<div class="h"><small>${esc(h.time)}</small><span>${wi(h.code, h.day)}</span><b>${deg(h.temp)}</b>
        <small class="rp">${h.rain >= 20 ? h.rain + ' %' : ''}</small></div>`).join('')}</div>
      <div class="wc tmr"><span class="wi">${WXI(m.code)}</span><div><b>${t('wx_tomorrow')}</b><small>${esc(cond(m))}</small>
        <small>${deg(m.t_min)} / ${deg(m.t_max)}${m.rain >= 20 ? ' · ☂ ' + m.rain + ' %' : ''}</small></div></div>`;
  }
  let wasWide = null;
  addEventListener('resize', () => {
    npSig = ''; renderNp(); const v = $('#view'); if (v) sizeGrids(v);
    const k = wide() + '|' + cmp();
    if (k !== wasWide) { wasWide = k; if (view === 'home') renderView(true); }
  });

  // ---- Sehen: Quellen + Fernbedienung ----
  function watchSrcHtml() {
    return (S.media.watch || []).map(s => `<div class="c ${S.rs.selected_device === s.id ? 'on' : ''}" onclick="C4.selectSource(${s.id},'watch')">
        <span class="ci">${C4.srcIcon(s, 'tv', 26)}</span><span class="cx"><b>${esc(s.name)}</b><small>${S.rs.selected_device === s.id ? t('now_playing') : ''}</small></span></div>`).join('');
  }
  function watchHtml() {
    const np = C4.nowPlaying(), active = np.on && np.watch, kr = C4.kodiRoom();
    if (kr) setTimeout(() => renderKodi(), 0);
    return `<div class="wv"><div class="wl"><div class="sheet"><div class="sh-h"><h2>${t('cat_watch')}</h2>
        ${active ? `<span class="hint">${esc(np.source)}</span>` : ''}</div>
        <div class="sh-b"><div id="knp">${kodiNpHtml()}</div><div class="secth">${t('select_source')}</div><div class="cards wsrc">${watchSrcHtml()}</div></div></div>
        ${kr ? `<div class="sheet ksheet"><div id="kodilib"></div></div>` : ''}</div>
      <div class="sheet rsheet"><div class="sh-h"><h2>${t('remote')}</h2>${active ? '' : `<span class="hint">${t('no_watch_src')}</span>`}</div>
        <div class="sh-b">${C4.remoteHtml()}</div></div></div>`;
  }
  function updateWatch() {
    const w = $('.wsrc'); if (w) w.innerHTML = watchSrcHtml();
    const k = $('#knp'); if (k) { const h = kodiNpHtml(); if (k.dataset.sig !== h) { k.dataset.sig = h; k.innerHTML = h; } }
    C4.remoteSync();
  }
  // Läuft auf KODI: Poster, Titel, Folge/Sender, Fortschritt
  function kodiNpHtml() {
    const np = C4.nowPlaying();
    if (!np.kodi || !np.title) return '';
    return `<div class="knp">${np.img ? `<img src="${esc(np.img)}" alt="" onerror="this.remove()">` : ''}
      <div><small>${t('now_playing')}${np.playState === 'pause' ? ' · ⏸' : ''}${np.res ? ' · ' + esc(np.res) : ''}</small><b>${esc(np.title)}</b>
      ${np.artist ? `<span class="ks">${esc(np.artist)}</span>` : ''}${progHtml(np)}</div></div>`;
  }

  // ---- KODI-Mediathek (nur lesen: stöbern, Details) ----
  let kb = { did: null, tab: null, q: '', sort: 'az', stack: [], items: null, total: 0, err: '', seq: 0 };
  const kTitle = { movies: 'k_movies', tvshows: 'k_tvshows', channels: 'k_channels', recordings: 'k_recordings', files: 'k_files' };
  function kodiTab(tab) { kb = { ...kb, tab, q: '', stack: [], items: null }; renderKodi(); }
  function kodiSort(s) { kb.sort = s; kb.items = null; renderKodi(); }
  function kodiOpen(i) {
    const it = kb.items[i];
    const top = kb.stack[kb.stack.length - 1];
    if (it.type === 'movie' || it.type === 'tvshow') kb.stack.push({ detail: it.type, id: it.id, title: it.title });
    else if (it.type === 'season') kb.stack.push({ season: it.id, show: top.id, title: it.title });
    else if (it.type === 'dir') kb.stack.push({ path: it.id, title: it.title });
    else return;
    kb.items = null; renderKodi();
    const el = $('#kodilib'); if (el) el.scrollIntoView({ block: 'nearest' });
  }
  function kodiBack() { kb.stack.pop(); kb.items = null; renderKodi(); }
  async function kodiMore() {
    const top = kb.stack[kb.stack.length - 1];
    const seq = kb.seq;
    const r = await C4.kodiLib(kb.did, kb.tab, { start: kb.items.length, limit: 60, q: kb.q, sort: kb.sort === 'new' ? 'new' : '', path: top && top.path });
    if (seq !== kb.seq) return;
    kb.items = kb.items.concat(r.items); renderKodi(false);
  }
  let kodiTimer;
  function kodiSearch(v) { clearTimeout(kodiTimer); kodiTimer = setTimeout(() => { kb.q = v.trim(); kb.items = null; renderKodi(); }, 350); }
  async function renderKodi(load = true) {
    const el = $('#kodilib'), kr = C4.kodiRoom(); if (!el || !kr) return;
    if (kb.did !== kr.did || !kr.tabs.includes(kb.tab)) kb = { ...kb, did: kr.did, tab: kr.tabs[0], q: '', stack: [], items: null };
    const top = kb.stack[kb.stack.length - 1];
    const seq = ++kb.seq;
    const head = `<div class="tabs ktabs">${kr.tabs.map(x => `<button class="${x === kb.tab ? 'on' : ''}" onclick="X.kodiTab('${x}')">${t(kTitle[x])}</button>`).join('')}</div>`;
    const tools = top ? `<div class="mh"><button class="btn" onclick="X.kodiBack()">${ic('chev_left', 16)} ${t('back')}</button><h2>${esc(top.title)}</h2></div>`
      : `<div class="mh"><h2>${t('kodi_lib')}</h2>${kb.total && kb.items ? `<span class="cnt">${kb.total}</span>` : ''}
        ${['movies', 'tvshows'].includes(kb.tab) ? `<div class="seg"><button class="${kb.sort === 'az' ? 'on' : ''}" onclick="X.kodiSort('az')">${t('k_az')}</button><button class="${kb.sort === 'new' ? 'on' : ''}" onclick="X.kodiSort('new')">${t('k_new')}</button></div>` : ''}
        ${kb.tab !== 'files' ? `<label class="search">${ic('search', 18)}<input id="kq" placeholder="${t('k_search')}" value="${esc(kb.q)}" autocomplete="off" oninput="X.kodiSearch(this.value)"></label>` : ''}</div>`;
    const keep = document.activeElement && document.activeElement.id === 'kq';
    const draw = body => {
      el.innerHTML = head + `<div class="sh-b">${tools}${body}</div>`;
      if (keep) { const q = $('#kq'); if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } }
    };
    if (!kr.ok) return draw(`<p class="empty">${t('k_offline')}</p>`);
    try {
      if (top && top.detail) {                          // Film / Serie: Details (+ Staffeln)
        if (load || !kb.det) draw(`<p class="empty">…</p>`);
        const [d, seasons] = await Promise.all([C4.kodiDetails(kb.did, top.detail, top.id),
          top.detail === 'tvshow' ? C4.kodiLib(kb.did, 'seasons', { parent: top.id }) : null]);
        if (seq !== kb.seq) return;
        kb.det = d; kb.items = seasons ? seasons.items : [];
        return draw(kodiDetailHtml(d) + (seasons ? `<div class="secth">${t('k_seasons')}</div>` + kodiGrid(kb.items, 'poster') : ''));
      }
      if (load || !kb.items) {
        draw(`<p class="empty">…</p>`);
        const kind = top && top.season != null ? 'episodes' : kb.tab;
        const r = await C4.kodiLib(kb.did, kind, { limit: 60, q: kb.q, sort: kb.sort === 'new' ? 'new' : '',
          parent: top && top.show, season: top && top.season, path: top && top.path });
        if (seq !== kb.seq) return;
        kb.items = r.items; kb.total = r.total;
      }
      const list = kb.tab === 'channels' || kb.tab === 'recordings' || kb.tab === 'files' || (top && top.season != null);
      const more = kb.items.length < kb.total && !(top && top.season != null) ? `<p style="text-align:center;margin-top:16px"><button class="btn" onclick="X.kodiMore()">${t('k_more')} (${kb.items.length}/${kb.total})</button></p>` : '';
      draw((kb.items.length ? kodiGrid(kb.items, list ? 'list' : 'poster') : `<p class="empty">${t('k_empty')}</p>`) + more);
      const cnt = el.querySelector('.mh .cnt'); if (!cnt && !top && kb.total) el.querySelector('.mh h2').insertAdjacentHTML('afterend', `<span class="cnt">${kb.total}</span>`);
    } catch (e) { if (seq === kb.seq) draw(`<p class="empty">${esc(e.message || t('k_offline'))}</p>`); }
  }
  function kodiGrid(items, mode) {
    const logo = kb.tab === 'channels' && !kb.stack.length;
    return `<div class="mgrid kgrid ${mode === 'list' ? 'list' : ''} ${logo ? 'logo' : ''}">${items.map((it, i) => {
      const click = ['movie', 'tvshow', 'season', 'dir'].includes(it.type) ? `onclick="X.kodiOpen(${i})"` : '';
      const pic = mode === 'list' && it.type === 'dir' ? `<div class="cover kp">${ic('folder_music', 22)}</div>` : C4.coverHtml('', it.title, 'cover kp', it.img);
      return `<div class="alb ${click ? '' : 'nc'} ${it.seen ? 'seen' : ''}" ${click} title="${esc(it.plot || it.title)}">${pic}
        <b>${esc(it.title)}</b><small>${logo && it.sub ? `<i>${t('k_now')}</i> ` : ''}${esc(it.sub || '')}${it.seen ? ' · ✓' : ''}</small></div>`;
    }).join('')}</div>`;
  }
  const hm = m => (m >= 60 ? Math.floor(m / 60) + ' h ' + String(m % 60).padStart(2, '0') + ' min' : t('k_min', m));
  function kodiDetailHtml(d) {
    const facts = [d.year, d.runtime ? hm(d.runtime) : '', (d.genre || []).join(', '), d.rating ? '★ ' + d.rating : '', d.resolution,
      d.episodes ? t('k_episodes', d.episodes) : '', d.seen ? t('k_seen') + ' ✓' : '', d.resume ? t('k_resume', mmss(d.resume)) : ''].filter(Boolean);
    return `<div class="kdet">${C4.coverHtml('', d.title, 'cover kp', d.img)}<div>
      <p class="kf">${facts.map(esc).join(' · ')}</p>${d.tagline ? `<p class="ktag">${esc(d.tagline)}</p>` : ''}
      <p class="kplot">${esc(d.plot || '')}</p>
      ${(d.director || []).length ? `<p class="kf"><b>${t('k_director')}:</b> ${esc(d.director.join(', '))}</p>` : ''}
      ${(d.cast || []).length ? `<p class="kf"><b>${t('k_cast')}:</b> ${esc(d.cast.join(', '))}</p>` : ''}
      ${d.type === 'movie' ? `<p class="note">${ic('info', 16)} ${t('k_play_soon')}</p>` : ''}</div></div>`;
  }

  // ---- Licht im ganzen Haus ----
  async function loadHouseLights() {
    const [devs, st] = await Promise.all([C4.api('/api/devices?type=light&enabled_only=true'), C4.api('/api/devices/state')]);
    C4.mergeLights(st);           // gleiche Halte-Logik wie im Raum (kein Zurückspringen beim Dimmen)
    house = { devs };
    if (view === 'cat:light') renderView(true);
  }

  // ---- Custom Buttons: seitliches Menü (wie die Striche in der App) ----
  let cbOpenNow = false;
  function cbOpen(e) {
    e.stopPropagation(); closeMenu();
    cbOpenNow = !cbOpenNow;
    const p = $('#cbp'); p.hidden = !cbOpenNow; if (!cbOpenNow) return;
    p.innerHTML = `<div class="cbh"><b>${t('custom_buttons')}</b><span>${esc(S.room.name)}</span>
        <button class="cbx" onclick="X.cbClose()">${ic('x', 18)}</button></div>`
      + S.buttons.map(sc => `<div class="secth">${esc(sc.name)}</div><div class="cbl">${sc.buttons.map(b =>
        `<button class="cbb" onclick="C4.buttonPress(${b.id})">${esc(b.name)}</button>`).join('')}</div>`).join('');
  }
  function cbClose() { cbOpenNow = false; const p = $('#cbp'); if (p) p.hidden = true; }

  // ---- Menü ----
  function menu(e) { e.stopPropagation(); menuOpen = !menuOpen; renderMenu(); }
  function closeMenu() { menuOpen = false; const m = $('#menu'); if (m) m.hidden = true; }
  function renderMenu() {
    const m = $('#menu'); m.hidden = !menuOpen; if (!menuOpen) return;
    const me = S.me;
    m.innerHTML = `<div class="who ${me.open_mode ? 'warn' : ''}">${me.open_mode ? '⚠ ' + t('open_mode') : ic('user', 14) + ' ' + esc(me.user) + ' · ' + esc(me.role)}</div>
      <button class="mi" onclick="X.closeMenu();X.go('home');C4.setEditing(!C4.S.editing)">${ic('pencil', 18)} ${S.editing ? t('done') : t('edit_home')}</button>
      <div class="mi">${ic('palette', 18)} ${t('theme')}<span class="seg"><button class="on">X4</button><button onclick="C4.setTheme('os3')" title="${t('os3_old_hint')}">${t('os3_old')}</button></span></div>
      <div class="mi">${ic('globe', 18)} ${t('language')}<span class="seg"><button class="${C4.lang === 'de' ? 'on' : ''}" onclick="C4.setLang('de')">DE</button>
        <button class="${C4.lang === 'en' ? 'on' : ''}" onclick="C4.setLang('en')">EN</button></span></div>
      <button class="mi" onclick="X.toggleWx()">${ic('sun', 18)} ${t('wx_bar')}<span class="seg"><button class="${wxOn() ? 'on' : ''}">${wxOn() ? t('on') : t('off')}</button></span></button>
      ${me.role === 'admin' ? `<a class="mi" href="/admin">${ic('gear', 18)} ${t('admin')}</a>` : ''}
      ${!me.open_mode ? `<button class="mi" onclick="C4.logout()">${ic('logout', 18)} ${t('logout')}</button>` : ''}`;
  }

  // ------------------------------------------------------------ Ereignisse
  C4.on('ready', mount);
  C4.on('room', () => {
    cbClose();
    house = null; mb = { mode: 'album', artist: null, genre: null, q: '', albumId: null };
    if (view.startsWith('cat:') && !C4.cats().some(c => 'cat:' + c.key === view)) view = 'home';
    renderAll();
  });
  const refresh = () => { renderPills(); renderRail(); renderNp(); renderView(false); };
  C4.on('state', refresh);
  C4.on('push', refresh);
  C4.on('power', () => { if (view === 'house' && !dragging) renderHouse(); });
  C4.on('layout', () => { if (view === 'home') renderView(true); });
  C4.on('favrooms', () => { if (view === 'house') renderHouse(); });
  C4.on('lang', () => { renderAll(); renderMenu(); tickClock(); });
  C4.start();

  return {
    go, pickRoom, menu, closeMenu, cbOpen, cbClose, kodiTab, kodiSort, kodiOpen, kodiBack, kodiMore, kodiSearch, toggleWx, mbSet, openAlbum, mbRe, mbBack, songJump, bluGo, bluInput, srcGo,
    grpPick, lt: (id, lvl) => C4.setLevel(id, lvl).then(() => { if (lightScope === 'house') loadHouseLights(); }),
    lightScope: s => { lightScope = s; if (s === 'house') loadHouseLights(); renderView(true); },
    lightTab: s => { lightTab = s; renderView(true); },
  };
})();
