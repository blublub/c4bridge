/* VERALTET (17.10.2026): wird nicht mehr weiterentwickelt – neue Funktionen nur noch in X4.
   Theme OS 3 — rendert nur; Zustand/Aktionen kommen aus ui/core.js (C4). */
'use strict';
const O = (() => {
  const S = C4.S, t = C4.t;
  let page = 'home';           // home | light | blind | comfort | security | camera | listen | watch
  let scope = 'room';          // Filter unten: room | all
  let lightTab = 'lights', climIdx = 0;
  let mb = { mode: 'album', artist: null, genre: null, q: '', albumId: null }, mbPrev = null, mbSeq = 0, searchTimer;
  const allDevs = {};          // Filter „Alle": Geräte aller Räume je Bereich
  let allAt = 0, dragging = false, drawerOpen = false, uid = 0;

  const fa = f => (typeof f === 'number' ? f : `'${f}'`);
  const PAGE_OF = { light: 'light', blind: 'blind', thermostat: 'comfort', security: 'security', camera: 'camera', listen: 'listen', watch: 'watch' };
  const TITLE = { light: 'os3_light', blind: 'cat_blind', comfort: 'cat_comfort', security: 'cat_security', camera: 'cat_camera', listen: 'cat_listen', watch: 'cat_watch' };
  const blindTxt = b => (b.moving ? t('moving') : b.known ? t('pct_open', b.level) : t('pos_unknown'));
  const modeLbl = m => { const v = t('mode_' + m); return v === 'mode_' + m ? m : v; };
  const rng = (val, data) => `<input type="range" class="rng" min="0" max="100" value="${val}" style="--p:${val}%" ${data} onclick="event.stopPropagation()">`;
  const favBtn = f => `<button class="fav ${C4.isFav(f) ? 'have' : ''}" title="${t('add_fav')}" onclick="event.stopPropagation();C4.toggleFav(${fa(f)})">${ic('star', 30)}</button>`;

  // ------------------------------------------------------------ glänzende OS-3-Icons
  const G = {
    bulb: on => { const u = ++uid; return `<svg viewBox="0 0 100 120"><defs><radialGradient id="gb${u}" cx=".4" cy=".35" r=".7">
      <stop offset="0" stop-color="#fff"/><stop offset=".6" stop-color="${on ? '#fff6c8' : '#dfe9ff'}"/><stop offset="1" stop-color="${on ? '#ffd860' : '#9fb4e6'}"/></radialGradient>
      <linearGradient id="gm${u}" x1="0" x2="1"><stop offset="0" stop-color="#8d96a8"/><stop offset=".5" stop-color="#e6eaf2"/><stop offset="1" stop-color="#7d8698"/></linearGradient></defs>
      ${on ? '<circle cx="50" cy="44" r="46" fill="#fff3a0" opacity=".28"/>' : ''}
      <path d="M50 6c-21 0-36 15-36 35 0 14 8 22 14 30 4 5 5 9 5 14h34c0-5 1-9 5-14 6-8 14-16 14-30 0-20-15-35-36-35z" fill="url(#gb${u})"/>
      <rect x="33" y="88" width="34" height="22" rx="4" fill="url(#gm${u})"/><path d="M33 94h34M33 100h34M33 106h34" stroke="#5d6577" stroke-width="1.6"/>
      <path d="M42 110h16l-3 7h-10z" fill="#6c7487"/></svg>`; },
    blind: open => `<svg viewBox="0 0 100 120"><rect x="10" y="6" width="80" height="108" rx="3" fill="#cfd8ea" stroke="#eef3ff" stroke-width="3"/>
      <rect x="15" y="11" width="70" height="${open ? 30 : 92}" fill="#9aa6bd"/>${Array.from({ length: open ? 5 : 15 }, (_, i) => `<path d="M15 ${15 + i * 6}h70" stroke="#b9c3d6" stroke-width="2"/>`).join('')}
      ${open ? '<rect x="15" y="41" width="70" height="62" fill="#3ec3ff"/><rect x="15" y="41" width="70" height="22" fill="#7fdcff" opacity=".6"/>' : ''}</svg>`,
    folder: () => `<svg viewBox="0 0 120 100" fill="none" stroke="#f2f6ff" stroke-width="5" stroke-linejoin="round">
      <path d="M6 22h38l8 8h62v62H6z"/><path d="M6 30h108"/><path d="M58 76V44l22-5v30"/><circle cx="51" cy="77" r="8" fill="#f2f6ff"/><circle cx="73" cy="70" r="8" fill="#f2f6ff"/></svg>`,
    therm: (v, heat) => { const u = ++uid; return `<svg viewBox="0 0 120 120"><defs><radialGradient id="gt${u}" cx=".5" cy=".3"><stop offset="0" stop-color="#2d3f8f"/><stop offset="1" stop-color="#121c55"/></radialGradient></defs>
      <circle cx="60" cy="60" r="56" fill="url(#gt${u})" stroke="${heat ? '#d0703e' : '#8a6e66'}" stroke-width="5"/>
      ${heat ? '<path d="M60 18c4 5 7 8 7 12a7 7 0 0 1-14 0c0-3 3-6 7-12z" fill="#ff8a3d"/>' : ''}
      <text x="60" y="76" text-anchor="middle" font-size="32" fill="#fff" font-family="Roboto,Segoe UI,sans-serif">${esc(v)}</text></svg>`; },
    shield: alert => { const u = ++uid; return `<svg viewBox="0 0 100 120"><defs><linearGradient id="gs${u}" x1="0" x2="1"><stop offset="0" stop-color="${alert ? '#c66' : '#7f93c6'}"/><stop offset=".5" stop-color="${alert ? '#f2a0a0' : '#b3c3ec'}"/><stop offset="1" stop-color="${alert ? '#b55' : '#6c7fb3'}"/></linearGradient></defs>
      <path d="M50 4l42 14v34c0 30-20 52-42 64C28 104 8 82 8 52V18z" fill="url(#gs${u})" stroke="#dbe5ff" stroke-width="2"/>
      <path d="M32 58l18-16 18 16v20H32z" fill="none" stroke="#eef3ff" stroke-width="4" stroke-linejoin="round"/></svg>`; },
    tv: () => `<svg viewBox="0 0 120 100"><rect x="6" y="10" width="108" height="66" rx="6" fill="#1a2550" stroke="#dfe8ff" stroke-width="4"/>
      <rect x="14" y="18" width="92" height="50" fill="#3a78f0" opacity=".7"/><path d="M40 92h40M60 76v16" stroke="#dfe8ff" stroke-width="5" stroke-linecap="round"/></svg>`,
    camera: () => `<svg viewBox="0 0 120 100"><rect x="8" y="22" width="76" height="56" rx="10" fill="#cfd8ea" stroke="#eef3ff" stroke-width="3"/>
      <circle cx="46" cy="50" r="17" fill="#1a2550"/><circle cx="46" cy="50" r="9" fill="#3a78f0"/><path d="M84 40l26-14v48L84 60z" fill="#b9c3d6"/></svg>`,
  };
  function catGlyph(key) {
    if (key === 'light') return G.bulb(C4.catActive('light'));
    if (key === 'blind') return G.blind(C4.devsOf('blind').some(d => C4.blind(d).open));
    if (key === 'thermostat') { const c = C4.devsOf('thermostat').map(C4.clim).find(x => x.temp != null); return G.therm(c ? C4.num1(c.temp) : '–', c && c.heating); }
    if (key === 'security') return G.shield(C4.catActive('security') && C4.summary('security') !== t('motion'));
    if (key === 'camera') return G.camera();
    if (key === 'listen') return G.folder();
    return G.tv();
  }

  // ------------------------------------------------------------ Gerüst
  function mount() {
    $('#app').innerHTML = `<div class="os3" id="os3"></div>`;
    document.addEventListener('pointerdown', e => { if (e.target.matches && e.target.matches('.rng')) dragging = true; });
    document.addEventListener('pointerup', () => setTimeout(() => { dragging = false; }, 400));
    document.addEventListener('input', e => {
      if (e.target.matches('.rng')) C4.rangeLive(e.target);
      if (e.target.id === 'mq') {
        clearTimeout(searchTimer);
        const q = e.target.value.trim();
        searchTimer = setTimeout(() => { mb = { ...mb, q, mode: q ? 'search' : 'album' }; renderMusic(); }, 300);
      }
    });
    document.addEventListener('change', e => {
      const r = e.target; if (!r.matches('.rng')) return;
      dragging = false;
      C4.rangeEnd(r);
      if (r.dataset.lvl) C4.setLevel(+r.dataset.lvl, +r.value);
      else if (r.dataset.blind) C4.blindLevel(+r.dataset.blind, +r.value);
      else if (r.dataset.vol) C4.volume(+r.value);
    });
    setInterval(() => { const c = $('#ck'); if (c) c.textContent = C4.fmtTime(); }, 10000);
    // Direktsprung per Anker, z. B. /#light, /#listen, /#comfort, /#edit
    const h = decodeURIComponent(location.hash.slice(1));
    if (h === 'edit') setTimeout(() => C4.setEditing(true), 0);
    else if (Object.values(PAGE_OF).includes(h)) page = h;
  }
  function go(p) {
    page = p; closeDrawer();
    if (scope === 'all' && ['light', 'blind', 'security'].includes(p)) loadAll(p);
    render();
  }
  function back() {
    if (page === 'listen' && mb.mode === 'detail') { mbBack(); return; }
    go('home');
  }

  // ------------------------------------------------------------ Rendern
  function render() {
    const root = $('#os3'); if (!root || !S.room) return;
    root.innerHTML = page === 'home' ? homeHtml() : subHtml();
    C4.wireCams(root);
    if (page === 'listen') renderMusic();
    renderNp();
    renderDrawer();
  }
  function refresh() {
    if (!S.room || dragging) return;
    const a = document.activeElement;
    if (a && a.matches && a.matches('input')) { renderNp(); return; }
    if (page === 'listen') { renderSrc(); renderNp(); return; }
    if (page === 'watch') { const w = $('#wsrc'); if (w) w.innerHTML = watchRows(); C4.remoteSync(); renderNp(); return; }
    const sc = $('#subc'), y = sc ? sc.scrollTop : 0;
    render();
    const sc2 = $('#subc'); if (sc2) sc2.scrollTop = y;
  }

  // ---- Homescreen ----
  function homeHtml() {
    const favs = C4.favList();
    const tiles = favs.map(tile).join('') + (S.editing ? `<div class="tile add" onclick="C4.showPicker()"><div class="gi">${ic('plus', 60)}</div><div class="nm">${t('add_fav')}</div></div>` : '');
    const n = favs.length + (S.editing ? 1 : 0);
    const temp = C4.devsOf('thermostat').map(C4.clim).find(c => c.temp != null);
    const np = C4.nowPlaying();
    return `<section class="scr home"><div class="ring4"></div>
      <div class="hh"><button class="burger" onclick="O.drawer()" title="${t('room')}">${ic('menu', 40)}</button>
        <div class="dots"><b class="on"></b></div>
        <div class="rtitle" onclick="O.drawer()">${esc(S.room.name)}</div>
        <button class="editbtn" onclick="C4.setEditing(!C4.S.editing)" title="${t('edit_home')}">${ic(S.editing ? 'x' : 'pencil', 26)}</button></div>
      <div class="tiles ${n <= 4 ? 'few' : ''}" style="--n:${Math.max(1, n)}">${tiles}</div>
      <div class="clock4" id="ck">${C4.fmtTime()}</div>
      ${np.on ? `<div class="homenp" onclick="O.go('listen')">${C4.coverHtml(np.artist, np.album || np.title || np.source, 'cover cov', np.img)}<span>${esc(np.title || np.source)}</span></div>` : ''}
      ${temp ? `<div class="wx4">${ic('thermostat', 34)} ${C4.num1(temp.temp)}°</div>` : ''}</section>`;
  }
  function tile(f) {
    const ed = `<button class="ed rm" onclick="event.stopPropagation();C4.rmFav(${fa(f)})">${ic('x', 14)}</button>
      <span class="ed mv"><button onclick="event.stopPropagation();C4.moveFav(${fa(f)},-1)">◀</button><button onclick="event.stopPropagation();C4.moveFav(${fa(f)},1)">▶</button></span>`;
    const T = (gi, nm, sb, tap, cls = '') => `<div class="tile ${cls}" onclick="${S.editing ? '' : tap}"><div class="gi">${gi}</div>
      <div class="nm">${esc(nm)}</div>${sb ? `<div class="sb">${esc(sb)}</div>` : ''}${ed}</div>`;
    if (typeof f === 'string' && f.startsWith('cat:')) {
      const k = f.slice(4), p = PAGE_OF[k];
      return T(catGlyph(k), t(TITLE[p]), k === 'security' && C4.catActive('security') ? C4.summary('security') : '', `O.go('${p}')`);
    }
    if (f === 'media') {
      const np = C4.nowPlaying();
      return np.on ? T(C4.coverHtml(np.artist, np.album || np.title || np.source, 'cover cov', np.img), np.title || np.source, np.artist || np.source, `O.go('listen')`)
        : T(G.folder(), t('library'), '', `O.go('listen')`);
    }
    if (f === 'security') return T(catGlyph('security'), t('cat_security'), C4.summary('security'), `O.go('security')`);
    const d = C4.dev(f) || C4.camDev(f); if (!d) return '';
    if (d.type === 'light') { const l = C4.light(d); return T(G.bulb(l.on), C4.label(d), l.on ? (l.dimmable ? l.level + ' %' : t('on')) : t('off'), `C4.toggleLight(${d.id})`); }
    if (d.type === 'blind') { const b = C4.blind(d); return T(G.blind(b.open), C4.label(d), blindTxt(b), `C4.blindToggle(${d.id})`); }
    if (d.type === 'thermostat') {
      const c = C4.clim(d), i = C4.devsOf('thermostat').indexOf(d);
      return T(G.therm(c.temp != null ? C4.num1(c.temp) : '–', c.heating), C4.label(d), '', `O.climAt(${i})`);
    }
    if (d.type === 'camera') return `<div class="tile cam" onclick="${S.editing ? '' : `O.go('camera')`}">${C4.camHtml(d).replace('class="cam-media"', 'class="cam-media snap"')}
      <div class="nm">${esc(C4.label(d))}</div>${ed}</div>`;
    return '';
  }

  // ---- Unterseiten ----
  function subHtml() {
    const head = (title, act = '') => `<div class="shead"><button class="hb home" onclick="O.go('home')" title="${t('home')}">${ic('home', 32)}</button>
      <button class="hb back" onclick="O.back()" title="${t('back')}">${ic('chev_left', 32)}</button><div class="ttl">${esc(title)}</div>${act}</div>`;
    const tabs = list => `<div class="tabs3">${list.map(([k, l, on]) => `<button class="${on ? 'on' : ''}" onclick="${k}">${esc(l)}</button>`).join('')}</div>`;
    const sect = name => `<div class="sect"><span>${esc(name)}</span></div>`;
    const filter = `<div class="filter"><button class="${scope === 'room' ? 'on' : ''}" onclick="O.scope('room')">${t('room')}</button>
      <span class="fi">${ic('menu', 22)}</span><button class="${scope === 'all' ? 'on' : ''}" onclick="O.scope('all')">${t('all')}</button></div>`;
    const wrap = (inner, extra = '') => `<section class="scr sub">${inner}${extra}<div class="npbar3" id="npb" hidden></div></section>`;
    const title = t(TITLE[page] || 'home');
    const groups = (type, rowFn) => {           // Raum oder alle Räume, je mit Abschnittskopf
      const out = [];
      const rooms = scope === 'all' ? S.rooms : [S.room];
      for (const r of rooms) {
        const ds = scope === 'all' ? (allDevs[page] || []).filter(d => d.room_id === r.id && type.includes(d.type)) : S.devs.filter(d => type.includes(d.type));
        if (!ds.length) continue;
        const rows = ds.map(d => rowFn(d, r.id !== S.room.id)).join('') + (ds.length % 2 ? '<div class="r pad"></div>' : '');
        out.push(sect(r.name) + `<div class="rows">${rows}</div>`);
      }
      return out.join('') || `<p class="empty3">${scope === 'all' && !allDevs[page] ? '…' : t('no_devices')}</p>`;
    };

    if (page === 'light') {
      const tb = tabs([[`O.lightTab('lights')`, t('light_sources'), lightTab === 'lights'], [`O.lightTab('scenes')`, t('scenes'), lightTab === 'scenes']]);
      const body = lightTab === 'scenes' ? `<p class="empty3">${t('scenes_soon')}</p>` : groups(['light'], lightRow);
      return wrap(head(title) + tb + `<div class="subc" id="subc">${body}</div>` + filter);
    }
    if (page === 'blind') return wrap(head(title) + `<div class="subc" id="subc">${groups(['blind'], blindRow)}</div>` + filter);
    if (page === 'security') return wrap(head(title) + `<div class="subc" id="subc">${groups(['contact', 'motion'], secRow)}</div>` + filter);
    if (page === 'comfort') {
      const ths = C4.devsOf('thermostat');
      const blinds = C4.devsOf('blind');
      if (!ths.length) return wrap(head(title) + `<div class="subc" id="subc">${groups(['blind'], blindRow)}</div>`);
      climIdx = Math.min(climIdx, ths.length - 1);
      const tb = tabs([...ths.map((d, i) => [`O.climAt(${i})`, ths.length > 1 ? C4.label(d) : t('cat_thermostat'), i === climIdx]),
        ...(blinds.length ? [[`O.go('blind')`, t('cat_blind'), false]] : [])]);
      return wrap(head(S.room.name) + tb + `<div class="subc" id="subc">${climHtml(ths[climIdx])}</div>`);
    }
    if (page === 'camera') return wrap(head(title) + `<div class="subc" id="subc"><div class="camg3">${C4.camList().map(d =>
      `<div>${C4.camHtml(d)}<div class="lbl">${esc(C4.label(d))}${d.other ? ` · ${esc(d.room)}` : ''}</div></div>`).join('')}</div></div>`);
    if (page === 'watch') return wrap(head(title) + sect(S.room.name) + `<div class="subc wv3" id="subc"><div class="rows one" id="wsrc">${watchRows()}</div>
        <div class="rpan">${C4.remoteHtml()}</div></div>`);
    if (page === 'listen') {
      const mt = MT();
      const on = m => mtOn(m);
      return wrap(head(title) + `<div class="srcbar" id="srcbar"></div>
        <div class="mtabs">${mt.map(([m, l]) => `<button data-m="${m}" class="${on(m) ? 'on' : ''}" onclick="O.mbSet({mode:'${m}',artist:null,genre:null,q:''})">${l}</button>`).join('')}
          <span class="sp"></span><label class="srch">${ic('search', 18)}<input id="mq" placeholder="${t('search_music')}" value="${esc(mb.q)}" autocomplete="off"></label></div>
        <div class="mwrap"><div class="mscroll" id="mscroll"></div><div class="az" id="az" hidden></div></div>`);
    }
    return wrap(head(title) + `<p class="empty3">${t('no_devices')}</p>`);
  }
  function lightRow(d, other) {
    const l = C4.light(d), lvl = l.on ? 0 : 100;
    return `<div class="r ${l.on ? 'on' : ''}" onclick="${S.editing ? '' : `C4.setLevel(${d.id},${lvl})`}"><div class="ri">${ic(l.on ? 'light_on' : 'light_off', 44)}</div>
      <div class="rn"><b>${esc(C4.label(d))}</b>${l.dimmable ? rng(l.level, `data-lvl="${d.id}"`) : `<small>${l.on ? t('on') : t('off')}</small>`}</div>
      ${l.dimmable ? `<span class="lvl">${l.on ? l.level + ' %' : t('off')}</span>` : ''}${other ? '' : favBtn(d.id)}</div>`;
  }
  function blindRow(d, other) {
    const b = C4.blind(d);
    return `<div class="r ${b.moving ? 'on' : ''}" style="cursor:default"><div class="ri">${ic(b.open ? 'blind_open' : 'blind_closed', 44)}</div>
      <div class="rn"><b>${esc(C4.label(d))}</b><small>${blindTxt(b)}${b.group ? ' · ' + t('group') : ''}</small>${b.hasLevel ? rng(b.level, `data-blind="${d.id}"`) : ''}</div>
      <span class="bb"><button onclick="C4.blindDo(${d.id},'open')">${ic('chev_up', 24)}</button><button onclick="C4.blindDo(${d.id},'stop')">${ic('stop', 20)}</button>
        <button onclick="C4.blindDo(${d.id},'close')">${ic('chev_down', 24)}</button></span>${other ? '' : favBtn(d.id)}</div>`;
  }
  function secRow(d) {
    const s = C4.sec(d);
    return `<div class="r ${s.active ? 'alert' : ''}" style="cursor:default"><div class="ri">${ic(s.icon, 44)}</div>
      <div class="rn"><b>${esc(C4.label(d))}</b><small>${esc(d.name)}</small></div>
      <span class="st" style="color:${!s.known ? 'var(--dim)' : s.active ? '#ffb3b3' : '#9dffc4'}">${esc(s.text)}</span></div>`;
  }
  function climHtml(d) {
    const c = C4.clim(d);
    const frac = v => Math.max(0, Math.min(1, (v - 5) / 25));
    const ang = c.sp != null ? (135 + 270 * frac(c.sp)) * Math.PI / 180 : null;
    const pin = ang != null ? `<div class="pin" style="left:${(50 + 45.2 * Math.cos(ang)).toFixed(1)}%;top:${(50 + 45.2 * Math.sin(ang)).toFixed(1)}%"><b>${C4.num1(c.sp)}</b></div>` : '';
    const u = ++uid;
    return `<div class="tstat">
      <div class="meta"><span>${ic('thermostat', 22)} ${c.temp != null ? C4.num1(c.temp) + '°' : '–'}</span>${c.humidity != null ? `<span>💧 ${Math.round(c.humidity)} %</span>` : ''}</div>
      <div class="tside left"><div class="modes">${c.modes.map(m => `<button class="tside ${c.mode.toLowerCase() === m.toLowerCase() ? 'on' : ''}" style="position:static" onclick="C4.climMode(${d.id},'${jsq(m)}')">
          <span class="b">${ic({ Heat: 'flame', Cool: 'snow', Off: 'power' }[m] || 'refresh', 40)}</span><span>${esc(modeLbl(m))}</span></button>`).join('')}</div></div>
      <div class="dial"><svg viewBox="0 0 420 420"><defs><linearGradient id="arc${u}" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#ff6a5c"/><stop offset="1" stop-color="#e4a08a"/></linearGradient></defs>
          <path d="M 75.6 344.4 A 190 190 0 1 1 344.4 344.4" fill="none" stroke="${c.heating ? `url(#arc${u})` : 'rgba(220,235,255,.55)'}" stroke-width="8" stroke-linecap="round"/></svg>
        <div class="core"><span>${c.temp != null ? C4.num1(c.temp) : '–'}</span></div>${pin}
        <div class="ud"><button onclick="C4.climStep(${d.id},-0.5)">${ic('chev_down', 44)}</button><button onclick="C4.climStep(${d.id},0.5)">${ic('chev_up', 44)}</button></div></div>
      <div class="tside right" onclick="C4.toggleFav(${d.id})"><span class="b">${ic(c.hold ? 'calendar' : 'star', 40)}</span>
        <span>${c.hold ? esc(c.hold) : C4.isFav(d.id) ? t('on_home') : t('add_fav')}</span></div></div>`;
  }

  // ---- Hören ----
  function renderSrc() {
    const el = $('#srcbar'); if (!el) return;
    const np = C4.nowPlaying();
    if (C4.isBluos()) {   // BluOS-Raum: Dienste + Eingänge des Players
      if (!S.bluTop) { C4.bluTop().then(renderSrc); el.innerHTML = ''; return; }
      const b = S.rs.bluos || {};
      el.innerHTML = S.bluTop.map((it, i) => [it, i]).filter(([it]) => (it.browse && !/^LocalMusic:/.test(it.browse)) || (it.input && it.play))
        .map(([it, i]) => {
          const live = np.on && (it.input ? b.service === 'Capture' && b.title === it.text : b.service && it.browse.startsWith(b.service + ':'));
          return `<button class="${mb.mode === 'blu' && mb.blu === i ? 'on' : ''} ${live ? 'live' : ''}" onclick="O.bluGo(${i})">${it.input ? ic('power', 16) + ' ' : ''}${esc(it.text)}</button>`;
        }).join('');
      return;
    }
    el.innerHTML = (S.media.listen || []).map(s => `<button class="${S.rs.selected_device === s.id ? 'on' : ''} ${S.rs.selected_device === s.id && np.on ? 'live' : ''}"
      onclick="C4.selectSource(${s.id},'listen')">${s.icon ? C4.srcIcon(s, 'music', 18) + ' ' : ''}${esc(s.name)}</button>`).join('');
  }
  async function renderMusic() {
    renderSrc();
    const el = $('#mscroll'), az = $('#az'); if (!el) return;
    const seq = ++mbSeq;
    az.hidden = true; el.onscroll = null;
    if (mb.mode === 'blu') { el.innerHTML = '<div class="blu3" id="bluv"></div>'; C4.bluStart($('#bluv'), S.bluTop[mb.blu].browse, S.bluTop[mb.blu].text); return; }
    try {
      const play = C4.canPlay();
      const note = play ? '' : `<p class="note3">${ic('info', 18)} ${t('not_c4room')}</p>`;
      if (mb.mode === 'detail') {
        const a = await C4.album(mb.albumId); if (seq !== mbSeq) return;
        el.innerHTML = `<div class="adet3"><div class="big">${C4.coverHtml(a.artist, a.title, 'cover', C4.coverUrl(a.img))}<h2>${esc(a.title)}</h2>
            <p>${esc(a.artist)}${a.year ? ' · ' + a.year : ''} · ${t('n_songs', a.songs.length)}</p>
            ${play ? `<button class="pill3" onclick="C4.playAlbum(${a.id})">${ic('play', 18)} ${t('play_album')}</button>
            <p style="font-size:1.1em;margin-top:.8em">${t('playing_in', esc(S.room.name))}</p>` : note}</div>
          <div class="tracks3">${a.songs.map((s, i) => `<div class="${s.id === mb.trackId ? 'hit' : ''}" id="trk-${s.id}" ${play ? `style="cursor:pointer" onclick="C4.playSong(${a.id},${i + 1},${s.id})"` : ''}><span class="n">${s.track_no || i + 1}</span><span class="nm3">${esc(s.name)}</span>
              ${play ? `<button class="pf3" onclick="event.stopPropagation();C4.playAlbum(${a.id},${i + 1},${s.id})">${ic('play', 16)} ${t('play_from')}</button>` : ''}</div>`).join('')}
            ${play ? `<p class="hint3">${t('track_hint')}</p>` : ''}</div></div>`;
        if (mb.trackId) { const h = document.getElementById('trk-' + mb.trackId); if (h) h.scrollIntoView({ block: 'center' }); }
      } else if (mb.mode === 'search') {
        const r = await C4.search(mb.q); if (seq !== mbSeq) return;
        let h = '';
        if (r.artists.length) h += `<div class="sect"><span>${t('sec_artists')}</span></div><div class="alist">${r.artists.map(x =>
          `<button class="ar" onclick="O.mbSet({mode:'album',artist:'${jsq(x.artist)}'})"><span class="av" style="${C4.coverStyle(x.artist, '')}">${esc(x.artist.split(/\s+/).map(w => w[0]).slice(0, 2).join(''))}</span>
            <span>${esc(x.artist)}</span><small>${t('n_albums', x.albums)}</small></button>`).join('')}</div>`;
        if (r.albums.length) h += `<div class="sect"><span>${t('sec_albums')}</span></div><div class="agrid ${C4.albView() === 'list' ? 'list' : ''}">${r.albums.map(albCard).join('')}</div>`;
        if (r.songs.length) h += `<div class="sect"><span>${t('sec_songs')}</span></div><div class="alist">${r.songs.map(x => songBtn({ i: x.id, n: x.name, a: x.artist, b: x.album_id, t: x.album, k: x.track_no })).join('')}</div>`;
        el.innerHTML = h || `<p class="empty3">${t('no_hits')}</p>`;
      } else if (mb.mode === 'song') {
        songs = await C4.browse('song'); if (seq !== mbSeq) return;
        el.innerHTML = note + `<div class="alist" id="songlist"></div><p class="empty3" id="songsen">…</p>`;
        shown = 0; moreSongs(150);
        el.onscroll = () => { if (el.scrollTop + el.clientHeight > el.scrollHeight - 900) moreSongs(); };
        az.hidden = false;
        const have = new Set(songs.map(x => C4.letterOf(x.n)));
        az.innerHTML = '#ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').map(c => `<button style="${have.has(c) ? '' : 'opacity:.35'}" onclick="O.jump('${c}')">${c}</button>`).join('');
      } else if (mb.mode === 'recent' || mb.mode === 'random') {
        const r = await (mb.mode === 'recent' ? C4.recent() : C4.randomAlbums()); if (seq !== mbSeq) return;
        el.innerHTML = note + `<p class="avbar">${mb.mode === 'random' ? `<button class="pill3" style="margin:0" onclick="O.mbSet({mode:'random'})">${ic('shuffle', 18)} ${t('reshuffle')}</button>` : ''}${C4.albViewBtn('O.mbRe()')}</p>`
          + (r.length ? `<div class="agrid ${C4.albView() === 'list' ? 'list' : ''}">${r.map(albCard).join('')}</div>` : `<p class="empty3">${t('no_recent')}</p>`);
      } else if (mb.mode === 'queue') {
        const d = await C4.queue(); if (seq !== mbSeq) return;
        const np = C4.nowPlaying(), blu = d.backend === 'bluos', cur = blu ? d.index : (d.songs || []).findIndex(x => x.name === np.title);
        el.innerHTML = d.songs && d.songs.length ? `<div class="qhead">${d.album ? C4.coverHtml(d.album.artist, d.album.title, 'cover', C4.coverUrl(d.album.img)) : ''}
            <div><h2>${esc(d.album ? d.album.title : t('queue'))}</h2><p>${esc(d.album ? d.album.artist : '')}</p></div></div>
          <div class="tracks3">${d.songs.map((x, i) => `<div class="${i === cur ? 'hit' : ''}"><span class="n">${i === cur ? ic('volume', 16) : (x.track_no || i + 1)}</span><span class="nm3">${esc(x.name)}</span>
            ${(blu || (d.album && play)) && i !== cur ? `<button class="pf3" onclick="${blu ? `C4.queueJump(${x.pos})` : `C4.playAlbum(${d.album && d.album.id},${i + 1},${x.id || 0})`}">${ic('play', 16)}</button>` : ''}</div>`).join('')}</div>`
          : `<p class="empty3">${t('queue_empty')}</p>`;
      } else if (mb.mode === 'artist' || mb.mode === 'genre') {
        const r = await C4.browse(mb.mode); if (seq !== mbSeq) return;
        el.innerHTML = `<div class="alist">${r.map(x => {
          const n = mb.mode === 'artist' ? x.artist : x.genre;
          return `<button class="ar" data-l="${esc((n[0] || '#').toUpperCase())}" onclick="O.mbSet({mode:'album',${mb.mode}:'${jsq(n)}'})">
            <span class="av" style="${C4.coverStyle(n, '')}">${esc(n.split(/\s+/).map(w => w[0]).slice(0, 2).join(''))}</span><span>${esc(n)}</span><small>${t('n_albums', x.albums)}</small></button>`;
        }).join('')}</div>`;
        if (mb.mode === 'artist') showAz(el);
      } else {
        const r = await C4.browse('album', mb.artist, mb.genre); if (seq !== mbSeq) return;
        el.innerHTML = note + `<p class="avbar">${mb.artist || mb.genre ? `<button class="pill3" style="margin:0" onclick="O.mbSet({mode:'album',artist:null,genre:null})">${ic('x', 16)} ${esc(mb.artist || mb.genre)}</button>` : ''}${C4.albViewBtn('O.mbRe()')}</p>`
          + `<div class="agrid ${C4.albView() === 'list' ? 'list' : ''}">${r.map(a => `<div class="alb" data-l="${esc((a.artist[0] || '#').toUpperCase())}" onclick="O.openAlbum(${a.id})">${C4.coverHtml(a.artist, a.title, 'cover', C4.coverUrl(a.img))}
            <div class="tt">${esc(a.title)}</div><div class="aa">${esc(a.artist)}</div></div>`).join('')}</div>`;
        if (!mb.artist && !mb.genre) showAz(el);
      }
    } catch (e) { if (seq === mbSeq) el.innerHTML = `<p class="empty3">${esc(e.message)}</p>`; }
  }
  function showAz(el) {
    const az = $('#az'); az.hidden = false;
    const have = new Set($$('[data-l]', el).map(x => x.dataset.l));
    az.innerHTML = '#ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').map(c => `<button style="${have.has(c) ? '' : 'opacity:.35'}" onclick="O.jump('${c}')">${c}</button>`).join('');
  }
  function jump(c) {
    const el = $('#mscroll'); if (!el) return;
    if (mb.mode === 'song' && songs) {            // Titel: bis zum Buchstaben nachladen
      const idx = songs.findIndex(x => (c === '#' ? C4.letterOf(x.n) === '#' : C4.letterOf(x.n) >= c));
      if (idx >= 0) moreSongs(idx + 60);
    }
    const hit = $$('[data-l]', el).find(x => (c === '#' ? !/[A-Z]/.test(x.dataset.l) : x.dataset.l >= c));
    if (hit) el.scrollTop = hit.offsetTop - el.offsetTop - 10;
  }
  function mbSet(p) {
    mb = { ...mb, ...p, trackId: null }; if (p.mode && p.mode !== 'search') mb.q = p.q ?? '';
    const q = $('#mq'); if (q && !p.q) q.value = mb.q;
    $$('.mtabs button[data-m]').forEach(b => b.classList.toggle('on', mtOn(b.dataset.m)));
    renderMusic(); const s = $('#mscroll'); if (s) s.scrollTop = 0;
  }
  function openAlbum(id, trackId) { if (mb.mode !== 'detail') mbPrev = { ...mb }; mb = { ...mb, mode: 'detail', albumId: id, trackId: trackId || null }; renderMusic(); const s = $('#mscroll'); if (s && !trackId) s.scrollTop = 0; }
  // Reiter + Hilfen für Hören
  const MT = () => [['album', t('albums')], ['artist', t('artists')], ['genre', t('genres')], ['song', t('tab_songs')],
    ['recent', t('recent')], ['random', t('random')], ...(C4.nowPlaying().on && C4.canPlay() ? [['queue', t('queue')]] : [])];
  const mtOn = m => (m === 'album' ? ['album', 'detail', 'search'].includes(mb.mode) && !(mb.mode === 'detail' && mbPrev && mbPrev.mode !== 'album') : mb.mode === m
    || (mb.mode === 'detail' && mbPrev && mbPrev.mode === m));
  const albCard = a => `<div class="alb" onclick="O.openAlbum(${a.id})">${C4.coverHtml(a.artist, a.title, 'cover', C4.coverUrl(a.img))}
      <div class="tt">${esc(a.title)}</div><div class="aa">${esc(a.artist)}</div></div>`;
  const songBtn = x => `<button class="ar" data-l="${C4.letterOf(x.n)}" onclick="${C4.canPlay() ? `C4.playSong(${x.b},0,${x.i})` : `O.openAlbum(${x.b},${x.i})`}"><span class="ni">${ic('note', 22)}</span>
      <span>${esc(x.n)}<br><small style="margin:0;font-size:.75em">${esc(x.a)}${x.t ? ' · ' + esc(x.t) : ''}</small></span>
      <span class="pf3" title="${t('open_album')}" onclick="event.stopPropagation();O.openAlbum(${x.b},${x.i})">${ic('folder_music', 16)}</span>
      ${C4.canPlay() ? `<span class="pf3" title="${t('play_from')}" onclick="event.stopPropagation();C4.playAlbum(${x.b},0,${x.i})">${ic('play', 16)} ${t('play_from')}</span>` : ''}</button>`;
  let songs = null, shown = 0;
  function moreSongs(upto) {
    const box = $('#songlist'); if (!box || !songs) return;
    const end = Math.min(songs.length, Math.max(upto || 0, shown + 150));
    if (end > shown) { box.insertAdjacentHTML('beforeend', songs.slice(shown, end).map(songBtn).join('')); shown = end; }
    const sen = $('#songsen'); if (sen) sen.hidden = shown >= songs.length;
  }
  function watchRows() {
    return (S.media.watch || []).map(s => `<div class="r ${S.rs.selected_device === s.id ? 'on' : ''}" onclick="C4.selectSource(${s.id},'watch')"><div class="ri">${C4.srcIcon(s, 'tv', 44)}</div>
        <div class="rn"><b>${esc(s.name)}</b><small>${S.rs.selected_device === s.id ? t('now_playing') : ''}</small></div></div>`).join('');
  }
  function mbBack() { mb = mbPrev || { mode: 'album', artist: null, genre: null, q: '' }; mbPrev = null; renderMusic(); }

  // ---- Now-Playing-Leiste (Unterseiten) ----
  function renderNp() {
    const el = $('#npb'); if (!el) return;
    const np = C4.nowPlaying();
    el.hidden = !np.on;
    if (!np.on || (dragging && el.contains(document.activeElement))) return;
    el.hidden = !np.on || (np.watch && page === 'watch');
    if (el.hidden) return;
    const tgt = np.watch ? 'watch' : 'listen';
    el.innerHTML = `<div class="cov" onclick="O.go('${tgt}')">${np.watch ? ic('remote', 30) : C4.coverHtml(np.artist, np.album || np.title || np.source, 'cover', np.img)}</div>
      <div class="ti" onclick="O.go('${tgt}')"><b>${esc(np.title || np.source || t('no_source'))}</b><span>${esc([np.artist, np.title ? '' : np.source, S.room.name].filter(Boolean).join(' · '))}</span></div>
      <div class="tp"><button onclick="C4.transport('SKIP_REV')">${ic('prev', 30)}</button>${C4.ppButtons('pp', 26)}
        <button onclick="C4.transport('SKIP_FWD')">${ic('next', 30)}</button></div>
      <div class="vol"><button onclick="C4.mute(${!np.muted})" style="color:#fff">${ic(np.muted ? 'vol_mute' : 'volume', 26)}</button>${rng(np.volume, 'data-vol="1"')}<span style="font-size:1.2em">${np.volume}</span></div>
      <button class="off" title="${t('room_off')}" onclick="C4.roomOff()">${ic('power', 24)}</button>`;
  }

  // ---- Raum-Menü ----
  function renderDrawer() {
    $$('.drawer, .scrim').forEach(x => x.remove());
    if (!drawerOpen) return;
    const me = S.me;
    const html = C4.roomGroups().map(g => `<h3>${esc(g.name)}</h3>` + g.rooms.map(r =>
      `<button class="di ${r.id === S.room.id ? 'on' : ''}" onclick="O.pickRoom(${r.id})">${esc(r.name)}${S.power[r.id] ? '<span class="pw"></span>' : ''}${C4.favStar(r.id)}</button>`).join('')).join('');
    const scr = document.createElement('div'); scr.className = 'scrim'; scr.onclick = closeDrawer;
    const dr = document.createElement('div'); dr.className = 'drawer';
    dr.innerHTML = `<div class="who">${me.open_mode ? '⚠ ' + t('open_mode') : esc(me.user) + ' · ' + esc(me.role)}</div>${html}
      <h3>${t('settings')}</h3>
      <button class="di" onclick="O.closeDrawer();O.go('home');C4.setEditing(!C4.S.editing)">${ic('pencil', 20)} ${S.editing ? t('done') : t('edit_home')}</button>
      ${S.editing && S.layout.can_set_default && !me.open_mode ? `<button class="di" onclick="C4.saveAsDefault()">${ic('star', 20)} ${t('save_default')}</button>` : ''}
      ${S.layout.personal ? `<button class="di" onclick="C4.resetLayout()">${ic('refresh', 20)} ${t('reset_layout')}</button>` : ''}
      <div class="di">${ic('palette', 20)} ${t('theme')}<span class="seg3"><button onclick="C4.setTheme('x4')">X4</button><button class="on" title="${t('os3_old_hint')}">${t('os3_old')}</button></span></div>
      <div class="di">${ic('globe', 20)} ${t('language')}<span class="seg3"><button class="${C4.lang === 'de' ? 'on' : ''}" onclick="C4.setLang('de')">DE</button>
        <button class="${C4.lang === 'en' ? 'on' : ''}" onclick="C4.setLang('en')">EN</button></span></div>
      ${me.role === 'admin' ? `<button class="di" onclick="location.href='/admin'">${ic('gear', 20)} ${t('admin')}</button>` : ''}
      ${!me.open_mode ? `<button class="di" onclick="C4.logout()">${ic('logout', 20)} ${t('logout')}</button>` : ''}`;
    document.body.append(scr, dr);
  }
  function closeDrawer() { if (drawerOpen) { drawerOpen = false; renderDrawer(); } }
  async function pickRoom(id) { drawerOpen = false; renderDrawer(); page = 'home'; await C4.loadRoom(id); }

  // ---- „Alle"-Filter: Geräte + Zustände aller Räume in die Kern-Zustände mischen ----
  async function loadAll(p) {
    try {
      if (p === 'light') {
        const [ds, st] = await Promise.all([C4.api('/api/devices?type=light&enabled_only=true'), C4.api('/api/devices/state')]);
        C4.mergeLights(st);
        allDevs.light = ds;
      } else if (p === 'blind') {
        const [ds, st] = await Promise.all([C4.api('/api/devices?type=blind&enabled_only=true'), C4.api('/api/blinds/state')]);
        for (const b of st) S.blind[b.id] = b;
        allDevs.blind = ds;
      } else if (p === 'security') {
        const [ds, st] = await Promise.all([C4.api('/api/devices?enabled_only=true'), C4.api('/api/contacts/state')]);
        for (const c of st) S.contact[c.id] = c;
        allDevs.security = ds.filter(d => d.type === 'contact' || d.type === 'motion');
      }
      allAt = Date.now();
      if (page === p && scope === 'all') refresh();
    } catch (e) { C4.toast(t('err') + ': ' + e.message); }
  }

  // ------------------------------------------------------------ Ereignisse
  C4.on('ready', mount);
  C4.on('room', () => {
    mb = { mode: 'album', artist: null, genre: null, q: '', albumId: null }; climIdx = 0;
    if (page !== 'home' && !C4.cats().some(c => PAGE_OF[c.key] === page)) page = 'home';
    render();
  });
  C4.on('state', () => {
    if (scope === 'all' && ['light', 'blind', 'security'].includes(page) && Date.now() - allAt > 5000) loadAll(page);
    refresh();
  });
  C4.on('push', refresh);
  C4.on('power', () => { if (drawerOpen) renderDrawer(); });
  C4.on('favrooms', () => { if (drawerOpen) renderDrawer(); });
  C4.on('layout', () => { if (page === 'home') render(); else renderDrawer(); });
  C4.on('lang', render);
  C4.start();

  return {
    go, back, pickRoom, closeDrawer, mbSet, openAlbum, mbBack, jump,
    bluGo: i => {
      const it = S.bluTop[i];
      if (it.input) { C4.bluPlay(it.play); return; }
      mb = { ...mb, mode: 'blu', blu: i, q: '' }; mbPrev = null;
      $$('.mtabs button[data-m]').forEach(b => b.classList.remove('on'));
      renderMusic(); const sc = $('#mscroll'); if (sc) sc.scrollTop = 0;
    },
    drawer: () => { drawerOpen = !drawerOpen; renderDrawer(); },
    scope: s => { scope = s; if (s === 'all') loadAll(page); render(); },
    lightTab: s => { lightTab = s; render(); },
    climAt: i => { climIdx = i; page = 'comfort'; render(); },
    mbRe: () => renderMusic(),
  };
})();
