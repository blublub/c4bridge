/* c4bridge · gemeinsamer UI-Kern für alle Themes (X4, OS 3).
 *
 * Hier liegt ALLES, was nicht nach Theme aussieht: Anmeldung/Einstellungen, Zustand des
 * aktuellen Raums, Polling + Push (SSE), Aktionen, Favoriten-Layout, Musikbibliothek,
 * Kameras, Übersetzungen, Einrichtungsassistent und der Favoriten-Auswahldialog.
 * Ein Theme (ui/x4.js, ui/os3.js) rendert nur und hört auf Ereignisse:
 *   C4.on('room' | 'state' | 'power' | 'push' | 'lang' | 'layout', fn);  C4.start();
 */
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const jsq = s => esc(String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'"));   // für onclick="f('…')"

const C4 = (() => {
  // ---------------------------------------------------------------- Zustand
  const S = {
    me: null, prefs: {}, status: null,
    rooms: [], room: null, devs: [], media: { listen: [], watch: [] }, scenes: [], buttons: [], learned: {},
    rs: {},            // Raumzustand: volume, muted, selected_device, power, media_*
    light: {}, blind: {}, clim: {}, contact: {}, sw: {}, temps: {}, cams: {}, power: {},
    layout: { rooms: {} }, editing: false,
  };
  const subs = {};
  const on = (ev, fn) => { (subs[ev] = subs[ev] || []).push(fn); };
  const emit = (ev, d) => (subs[ev] || []).forEach(f => { try { f(d); } catch (e) { console.error(ev, e); } });

  // ---------------------------------------------------------------- HTTP
  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    if (r.status === 401) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
      throw new Error(t('not_logged_in'));
    }
    if (!r.ok) {
      let m = await r.text();
      try { m = JSON.parse(m).detail || m; } catch { /* Text */ }
      throw new Error(m);
    }
    return (r.headers.get('content-type') || '').includes('json') ? r.json() : r.text();
  }
  const json = (method, b) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) });
  const post = (p, b) => api(p, json('POST', b));
  const put = (p, b) => api(p, json('PUT', b));
  const del = p => api(p, { method: 'DELETE' });

  // ---------------------------------------------------------------- Sprache
  const I18N = {
    de: {
      favorites: 'Favoriten', other_floor: 'Weitere', house: 'Haus', room: 'Raum', all: 'Alle',
      cat_light: 'Licht', cat_blind: 'Beschattung', cat_thermostat: 'Klima', cat_comfort: 'Komfort',
      cat_security: 'Sicherheit', cat_camera: 'Kameras', cat_listen: 'Hören', cat_watch: 'Sehen', cat_switch: 'Schalter', sw_unknown: 'Status unbekannt', relays: 'Relais', locks_sensors: 'Kontakte & Melder', custom_buttons: 'Weitere Funktionen', no_scenes: 'Für diesen Raum sind keine Lichtszenen eingerichtet.', scene_on: 'aktiv', scene_q: 'Szene „{0}“ auslösen?', button_q: '„{0}“ drücken?', pressed: 'ausgelöst ✓', mix: 'Zufall (Mix)', rep_off: 'Wiederholen: aus', rep_all: 'Wiederholen: alle', rep_one: 'Wiederholen: dieser Titel', mix_na: 'für diese Quelle nicht verfügbar', cat_more: 'Weitere', l_unknown: 'unbekannt', l_low: 'niedrig', l_ok: 'ok', l_play: 'spielt', l_pause: 'Pause', l_stop: 'gestoppt', l_learned: 'angelernt', mtx_q: 'Kamera-Livestream: MediaMTX für {0} herunterladen (ca. 30 MB, von GitHub, Prüfsumme wird geprüft)?', mtx_load: 'Herunterladen', mtx_loading: 'MediaMTX wird geladen und geprüft …', mtx_ok: 'Kamera-Livestream bereit (MediaMTX {0}, {1})', mtx_none: 'Für {0} gibt es kein MediaMTX – Kameras zeigen Standbilder.', kodi_lib: 'Mediathek', k_movies: 'Filme', k_tvshows: 'Serien', k_channels: 'Live-TV', k_recordings: 'Aufnahmen', k_files: 'Dateien', k_az: 'A–Z', k_new: 'Neu', k_more: 'Mehr laden', k_search: 'Suchen …', k_offline: 'KODI ist gerade nicht erreichbar.', k_empty: 'Nichts gefunden.', k_seen: 'gesehen', k_min: '{0} min', k_seasons: 'Staffeln', k_episodes: '{0} Folgen', k_cast: 'Darsteller', k_director: 'Regie', k_now: 'jetzt', k_resume: 'angesehen bis {0}', k_play_soon: 'Abspielen aus der Mediathek folgt – bis dahin über die Fernbedienung.',
      os3_light: 'Beleuchtung', on: 'an', off: 'aus', n_on: '{0} an', all_off: 'alle aus',
      pct_open: '{0} % offen', closed: 'zu', open: 'offen', moving: 'fährt …', pos_unknown: 'Position unbekannt',
      group: 'Gruppe', heating: 'heizt', setpoint: 'Soll', mode_Off: 'Aus', mode_Heat: 'Heizen',
      mode_Cool: 'Kühlen', mode_Auto: 'Auto', hold: 'Halten', humidity: 'Luftfeuchte', actual: 'Ist',
      wait_reading: 'warte auf Messwert …', all_closed: 'alles zu', n_open: '{0} offen', motion: 'Bewegung',
      motion_yes: 'Bewegung erkannt', motion_no: 'ruhig', no_status: 'kein Status',
      window_open: 'OFFEN', window_closed: 'geschlossen',
      sources: 'Quellen', library: 'Unsere Musik', albums: 'Alben', artists: 'Interpreten', genres: 'Genres',
      search_music: 'Album, Interpret, Titel …', no_hits: 'keine Treffer', songs: 'Titel', n_albums: '{0} Alben',
      play_album: 'Album abspielen', playing_in: 'spielt in {0}', album_started: 'Album gestartet ✓',
      play_exp: 'Album-Start ist noch experimentell', no_source: 'keine Quelle', nothing_active: 'nichts aktiv',
      line_in: 'keine Titelinfo', listen_start: 'Hören starten', room_off: 'Raum aus', volume: 'Lautstärke',
      mute: 'Stumm', now_playing: 'Läuft gerade', select_source: 'Quelle wählen',
      live: 'LIVE', cam_err: 'Kamerabild nicht verfügbar', live_err: 'Livestream nicht erreichbar',
      edit_home: 'Startseite bearbeiten', done: 'Fertig', add_fav: 'Favorit hinzufügen', fav_added: 'Favorit angelegt ✓',
      fav_have: 'schon auf der Startseite', reset_layout: 'Hausstandard wiederherstellen',
      save_default: 'als Hausstandard speichern', layout_default_saved: 'Als Hausstandard gespeichert ✓',
      edit_hint: 'Bearbeiten: ✕ entfernt, ◀ ▶ verschiebt, ＋ fügt hinzu, Kachel ziehen verschiebt, Ecke unten rechts ändert die Größe',
      grp_pick: 'Geräte in dieser Kachel', grp_n: '{0} Geräte', grp_size: 'Größe ziehen', apply: 'Übernehmen', grp_show: 'Darstellung', grp_auto: 'Automatisch', grp_btn: 'Tasten', grp_sl: 'Schieber', grp_both: 'Beides',
      theme: 'Design', language: 'Sprache', admin: 'Admin', logout: 'Abmelden', open_mode: 'kein Login eingerichtet',
      scenes: 'Szenarien', scenes_soon: 'Szenen folgen, sobald sie im Composer als Buttons angelegt sind.',
      light_sources: 'Lichtquellen', no_devices: 'Keine Geräte dieser Art in diesem Raum.',
      greeting_0: 'Gute Nacht', greeting_1: 'Guten Morgen', greeting_2: 'Guten Tag', greeting_3: 'Guten Abend',
      not_ready: 'Die Bridge ist noch nicht eingerichtet – bitte einen Admin fragen.',
      wx_bar: 'Wetter unten', wx_tomorrow: 'Morgen', wx_feels: 'gefühlt',
      wiz_place: 'Ort für das Wetter (optional)', wiz_find: 'Suchen', wiz_noplace: 'Kein Ort gefunden',
      room_off_q: '{0} wirklich ausschalten?', yes: 'Ja', no: 'Abbrechen',
      os3_old: 'OS 3 (alt)', os3_old_hint: 'veraltet – wird nicht mehr weiterentwickelt', not_logged_in: 'nicht angemeldet',
      err: 'Fehler', back: 'Zurück', home: 'Start', close: 'Schließen', more: 'Mehr',
      rooms_on: '{0} Räume aktiv', lights_on_rooms: '{0} Lichter an', settings: 'Einstellungen',
      wiz_welcome: 'Willkommen bei c4bridge', wiz_cfg: 'Control4-Konto eintragen – danach wird das Projekt abgerufen.',
      wiz_noproj: 'Noch kein Projekt importiert – rufen wir es ab.', c4_email: 'Control4-E-Mail',
      c4_pass: 'Control4-Passwort', c4_ip: 'Controller-IP', test: 'Testen', testing: 'teste Anmeldung (ein Versuch) …',
      save_scan: 'Speichern & Projekt abrufen', wiz_ok: '✓ angemeldet – Controller:', wiz_fail: '✗ Anmeldung fehlgeschlagen',
      wiz_saving: 'speichere & rufe Projekt ab (bis ~30 s) …', wiz_imported: 'importiert – lade Oberfläche …',
      pick_title: 'Zur Startseite hinzufügen', pick_cats: 'Bereiche', pick_devs: 'Geräte',
      kind_album: 'Album', kind_song: 'Titel', search_res: 'Suche „{0}“', on_home: 'auf der Startseite ✓',
      tab_songs: 'Titel', recent: 'Zuletzt gespielt', random: 'Zufällig', reshuffle: 'Neu mischen',
      queue: 'Warteschlange', play_from: 'Ab hier', r_ctl: 'Steuerelemente', r_num: 'Tastenfeld', r_menu: 'Menü', r_cancel: 'Abbrechen', r_info: 'Info', r_back: 'Zurück',
      r_vol: 'Lauts&shy;tärke', r_ch: 'Kanal', r_ok: 'Auswählen', r_enter: 'Eingabetaste', r_guide: 'Guide',
      song_started: 'Titel gestartet ✓', play_song: 'Nur diesen Titel abspielen', view_list: 'Als Liste', view_grid: 'Als Kacheln', open_album: 'Album öffnen',
      song_c4: 'Control4 spielt keine Einzeltitel – Album ab Titel {0} gestartet ✓', track_started: 'Album ab Titel {0} gestartet ✓', track_started_name: 'Album ab „{0}" gestartet ✓',
      track_hint: '„Ab hier“ startet das Album und springt dann vor (Control4 kennt keinen Einzeltitel-Befehl).',
      not_c4room: 'In diesem Raum gibt es keine Musikquelle, die die Bridge abspielen kann.',
      services: 'Dienste', inputs: 'Eingänge', load_more: 'Mehr laden', play_all: 'Alles abspielen',
      search_in: 'In {0} suchen …', bluos_lib: 'Bibliothek', blu_dead: 'Am BluOS-Player nicht eingerichtet bzw. nicht erreichbar', bluos_off: 'BluOS-Player nicht erreichbar',
      started: 'Läuft ✓',
      no_recent: 'Noch nichts über die Bridge gespielt.', n_songs: '{0} Titel', queue_empty: 'Keine Warteschlange.',
      remote: 'Fernbedienung', no_watch_src: 'Erst eine Quelle wählen.', tv_off: 'TV aus', channel: 'Kanal',
      sec_artists: 'Interpreten', sec_albums: 'Alben', sec_songs: 'Titel', more_hits: '… weitere {0}',
      fav_room: 'Lieblingsraum (oben in der Liste)', fav_room_on: 'Lieblingsraum ✓', fav_room_off: 'kein Lieblingsraum mehr',
    },
    en: {
      favorites: 'Favorites', other_floor: 'Other', house: 'House', room: 'Room', all: 'All',
      cat_light: 'Lights', cat_blind: 'Shades', cat_thermostat: 'Climate', cat_comfort: 'Comfort',
      cat_security: 'Security', cat_camera: 'Cameras', cat_listen: 'Listen', cat_watch: 'Watch', cat_switch: 'Switches', sw_unknown: 'status unknown', relays: 'Relays', locks_sensors: 'Contacts & sensors', custom_buttons: 'More functions', no_scenes: 'No lighting scenes are set up for this room.', scene_on: 'active', scene_q: 'Run scene “{0}”?', button_q: 'Press “{0}”?', pressed: 'triggered ✓', mix: 'Shuffle (mix)', rep_off: 'Repeat: off', rep_all: 'Repeat: all', rep_one: 'Repeat: this track', mix_na: 'not available for this source', cat_more: 'More', l_unknown: 'unknown', l_low: 'low', l_ok: 'ok', l_play: 'playing', l_pause: 'paused', l_stop: 'stopped', l_learned: 'learned', mtx_q: 'Camera live stream: download MediaMTX for {0} (about 30 MB, from GitHub, checksum verified)?', mtx_load: 'Download', mtx_loading: 'Downloading and verifying MediaMTX …', mtx_ok: 'Camera live stream ready (MediaMTX {0}, {1})', mtx_none: 'No MediaMTX for {0} – cameras show still images.', kodi_lib: 'Library', k_movies: 'Movies', k_tvshows: 'TV shows', k_channels: 'Live TV', k_recordings: 'Recordings', k_files: 'Files', k_az: 'A–Z', k_new: 'New', k_more: 'Load more', k_search: 'Search …', k_offline: 'KODI is not reachable right now.', k_empty: 'Nothing found.', k_seen: 'watched', k_min: '{0} min', k_seasons: 'Seasons', k_episodes: '{0} episodes', k_cast: 'Cast', k_director: 'Director', k_now: 'now', k_resume: 'watched up to {0}', k_play_soon: 'Playing from the library is coming – use the remote for now.',
      os3_light: 'Lighting', on: 'on', off: 'off', n_on: '{0} on', all_off: 'all off',
      pct_open: '{0} % open', closed: 'closed', open: 'open', moving: 'moving …', pos_unknown: 'position unknown',
      group: 'group', heating: 'heating', setpoint: 'Setpoint', mode_Off: 'Off', mode_Heat: 'Heat',
      mode_Cool: 'Cool', mode_Auto: 'Auto', hold: 'Hold', humidity: 'Humidity', actual: 'Now',
      wait_reading: 'waiting for reading …', all_closed: 'all closed', n_open: '{0} open', motion: 'Motion',
      motion_yes: 'motion detected', motion_no: 'quiet', no_status: 'no status',
      window_open: 'OPEN', window_closed: 'closed',
      sources: 'Sources', library: 'Our music', albums: 'Albums', artists: 'Artists', genres: 'Genres',
      search_music: 'Album, artist, track …', no_hits: 'no results', songs: 'tracks', n_albums: '{0} albums',
      play_album: 'Play album', playing_in: 'plays in {0}', album_started: 'Album started ✓',
      play_exp: 'Album start is still experimental', no_source: 'no source', nothing_active: 'nothing active',
      line_in: 'no track info', listen_start: 'Start listening', room_off: 'Room off', volume: 'Volume',
      mute: 'Mute', now_playing: 'Now playing', select_source: 'Select source',
      live: 'LIVE', cam_err: 'Camera image unavailable', live_err: 'Live stream unreachable',
      edit_home: 'Edit home screen', done: 'Done', add_fav: 'Add favorite', fav_added: 'Favorite added ✓',
      fav_have: 'already on the home screen', reset_layout: 'Restore house default',
      save_default: 'save as house default', layout_default_saved: 'Saved as house default ✓',
      edit_hint: 'Edit: ✕ removes, ◀ ▶ moves, ＋ adds, drag a tile to move it, its bottom-right corner to resize',
      grp_pick: 'Devices in this tile', grp_n: '{0} devices', grp_size: 'Drag to resize', apply: 'Apply', grp_show: 'Display', grp_auto: 'Automatic', grp_btn: 'Buttons', grp_sl: 'Sliders', grp_both: 'Both',
      theme: 'Design', language: 'Language', admin: 'Admin', logout: 'Sign out', open_mode: 'no login configured',
      scenes: 'Scenes', scenes_soon: 'Scenes will appear once they exist as buttons in Composer.',
      light_sources: 'Lights', no_devices: 'No devices of this kind in this room.',
      greeting_0: 'Good night', greeting_1: 'Good morning', greeting_2: 'Good afternoon', greeting_3: 'Good evening',
      not_ready: 'The bridge is not set up yet – please ask an admin.',
      wx_bar: 'Weather bar', wx_tomorrow: 'Tomorrow', wx_feels: 'feels',
      wiz_place: 'Place for the weather (optional)', wiz_find: 'Search', wiz_noplace: 'No place found',
      room_off_q: 'Really switch off {0}?', yes: 'Yes', no: 'Cancel',
      os3_old: 'OS 3 (old)', os3_old_hint: 'deprecated – no longer developed', not_logged_in: 'not signed in',
      err: 'Error', back: 'Back', home: 'Home', close: 'Close', more: 'More',
      rooms_on: '{0} rooms active', lights_on_rooms: '{0} lights on', settings: 'Settings',
      wiz_welcome: 'Welcome to c4bridge', wiz_cfg: 'Enter the Control4 account – the project is fetched afterwards.',
      wiz_noproj: 'No project imported yet – let\'s fetch it.', c4_email: 'Control4 e-mail',
      c4_pass: 'Control4 password', c4_ip: 'Controller IP', test: 'Test', testing: 'testing login (one attempt) …',
      save_scan: 'Save & fetch project', wiz_ok: '✓ logged in – controller:', wiz_fail: '✗ login failed',
      wiz_saving: 'saving & fetching project (up to ~30 s) …', wiz_imported: 'imported – loading interface …',
      pick_title: 'Add to home screen', pick_cats: 'Areas', pick_devs: 'Devices',
      kind_album: 'Album', kind_song: 'Track', search_res: 'Search “{0}”', on_home: 'on home screen ✓',
      tab_songs: 'Tracks', recent: 'Recently played', random: 'Random', reshuffle: 'Shuffle again',
      queue: 'Queue', play_from: 'From here', track_started: 'Album started at track {0} ✓', track_started_name: 'Album started at "{0}" ✓',
      track_hint: '“From here” starts the album and then skips ahead (Control4 has no single-track command).',
      r_ctl: 'Controls', r_num: 'Keypad', r_menu: 'Menu', r_cancel: 'Cancel', r_info: 'Info', r_back: 'Back',
      r_vol: 'Vol', r_ch: 'Ch', r_ok: 'Select', r_enter: 'Enter', r_guide: 'Guide',
      song_started: 'Track started ✓', play_song: 'Play just this track', view_list: 'Show as list', view_grid: 'Show as tiles', open_album: 'Open album', song_c4: 'Control4 cannot play single tracks – album started at track {0} ✓',
      not_c4room: 'This room has no music source the bridge can play.',
      services: 'Services', inputs: 'Inputs', load_more: 'Load more', play_all: 'Play all',
      search_in: 'Search {0} …', bluos_lib: 'Library', blu_dead: 'Not set up on the BluOS player or unreachable', bluos_off: 'BluOS player unreachable',
      started: 'Playing ✓',
      no_recent: 'Nothing played via the bridge yet.', n_songs: '{0} tracks', queue_empty: 'No queue.',
      remote: 'Remote', no_watch_src: 'Select a source first.', tv_off: 'TV off', channel: 'Channel',
      sec_artists: 'Artists', sec_albums: 'Albums', sec_songs: 'Tracks', more_hits: '… {0} more',
      fav_room: 'Favorite room (top of the list)', fav_room_on: 'Favorite room ✓', fav_room_off: 'no longer a favorite',
    },
  };
  let lang = 'de';
  const t = (k, ...a) => {
    let s = (I18N[lang] && I18N[lang][k]) ?? I18N.de[k] ?? k;
    a.forEach((v, i) => { s = s.replace('{' + i + '}', v); });
    return s;
  };
  const locale = () => (lang === 'en' ? 'en-GB' : 'de-DE');
  const num1 = v => Number(v).toLocaleString(locale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fmtTime = (d = new Date()) => d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
  const fmtDate = (d = new Date(), long = false) => d.toLocaleDateString(locale(),
    long ? { weekday: 'long', day: 'numeric', month: 'long' } : { weekday: 'short', day: 'numeric', month: 'short' });
  const greeting = () => { const h = new Date().getHours(); return t('greeting_' + (h < 5 ? 0 : h < 11 ? 1 : h < 18 ? 2 : 3)); };

  // ---------------------------------------------------------------- Start
  let theme = window.C4_THEME || 'x4';
  async function start() {
    try { S.me = await api('/api/me'); } catch { S.me = null; }
    if (!S.me || !S.me.authenticated) { location.href = '/login?next=/'; return; }
    S.prefs = S.me.prefs || {};
    lang = S.prefs.lang || storeGet('c4lang') || 'de';
    document.documentElement.lang = lang;
    // Theme: ?theme=… (alte /os3-Links) speichert die Wahl; sonst gilt die Benutzer-Einstellung
    const q = new URLSearchParams(location.search).get('theme');
    if (q && q !== S.prefs.theme) { await setPref('theme', q); }
    if (q) history.replaceState(null, '', '/');
    const want = q || S.prefs.theme || 'x4';
    storeSet('c4theme', want);
    if (want !== theme) { location.reload(); return; }

    S.status = await api('/api/status').catch(() => null);
    if (!S.status || !S.status.control4.configured || S.status.inventory.rooms === 0) {
      if (S.me.role === 'admin') showSetup(!!(S.status && S.status.control4.configured));
      else document.body.innerHTML = `<p style="padding:40px;text-align:center">${t('not_ready')}</p>`;
      return;
    }
    const [rooms, layout] = await Promise.all([api('/api/rooms'), api('/api/ui/layout').catch(() => ({ rooms: {} }))]);
    S.rooms = rooms.filter(r => r.enabled && r.device_count > 0);
    S.layout = layout && layout.rooms ? layout : { rooms: {} };
    S.layout.sizes = S.layout.sizes || {};
    api('/api/cameras').then(cs => { for (const c of cs) S.cams[c.id] = c; emit('layout'); emit('state'); }).catch(() => { });
    const pick = S.rooms.find(r => r.id === S.prefs.room) || S.rooms.find(r => isFavRoom(r.id)) || S.rooms[0];
    emit('ready');
    await loadRoom(pick.id, false);
    // Push-first: Änderungen meldet die Bridge per SSE (/api/state/stream) → sofort neu laden.
    // Regelmäßiges Abfragen nur noch als Sicherheitsnetz (15 s), ohne Push wie früher alle 5 s.
    setInterval(() => {
      // Raum an: Quelle kommt nicht immer als Push (z. B. Netflix im TV) → 5 s wie die Control4-App
      const on = S.rs && (S.rs.selected_device || S.rs.power);
      const every = push.ok && !on ? 15000 : 5000;
      if (Date.now() - push.lastPoll >= every - 200) { poll(); pollPower(); }
    }, 5000);
    pollPower();
    setInterval(() => emit('tick'), 15000);
    listen();
    listenState();
  }

  const storeGet = k => { try { return localStorage.getItem(k); } catch { return null; } };
  const storeSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* privat */ } };

  async function setPref(k, v) {
    S.prefs[k] = v;
    try { await put('/api/me/prefs', { [k]: v }); } catch { /* offline */ }
  }
  // Alben als Kacheln oder kompakte Liste (pro Gerät gemerkt)
  const albView = () => storeGet('c4albview') === 'list' ? 'list' : 'grid';
  function albViewBtn(js) {
    const l = albView() === 'list';
    return `<button class="avt" title="${t(l ? 'view_grid' : 'view_list')}" onclick="C4.toggleAlbView();${js}">${ic(l ? 'grid' : 'list', 18)}</button>`;
  }
  const toggleAlbView = () => storeSet('c4albview', albView() === 'list' ? 'grid' : 'list');
  async function setTheme(v) { await setPref('theme', v); storeSet('c4theme', v); location.reload(); }
  async function setLang(v) {
    lang = v === 'en' ? 'en' : 'de';
    storeSet('c4lang', lang); document.documentElement.lang = lang;
    emit('lang'); await setPref('lang', lang);
  }
  async function logout() { await post('/api/logout').catch(() => { }); location.href = '/login'; }

  // ---------------------------------------------------------------- Raum laden + Polling
  let roomSeq = 0;
  async function loadRoom(id, remember = true) {
    const seq = ++roomSeq;
    S.room = S.rooms.find(r => r.id === id) || S.room;
    S.rs = {};
    holdTitle(null);
    const [devs, media, backend, scenes, buttons] = await Promise.all([
      api(`/api/devices?room_id=${S.room.id}&enabled_only=true`),
      api(`/api/rooms/${S.room.id}/media`).catch(() => ({ listen: [], watch: [] })),
      api(`/api/rooms/${S.room.id}/backend`).catch(() => ({ type: 'none' })),
      api(`/api/rooms/${S.room.id}/scenes`).catch(() => []),
      api(`/api/rooms/${S.room.id}/buttons`).catch(() => []),
    ]);
    if (seq !== roomSeq) return;           // inzwischen anderer Raum gewählt
    S.devs = devs; S.media = media; S.backend = backend; S.bluTop = null; bluStack = [];
    if (!S.kodi) await kodiLoad();
    S.scenes = scenes; S.buttons = buttons;
    emit('room');
    if (remember) setPref('room', S.room.id);
    await poll();
  }

  let polling = false, pollAgain = false;
  const push = { ok: false, lastPoll: 0 };
  async function poll() {
    if (!S.room || document.hidden) return;
    if (polling) { pollAgain = true; return; }   // Hinweis während einer laufenden Abfrage nicht verlieren
    polling = true; pollAgain = false; push.lastPoll = Date.now();
    const rid = S.room.id;
    try {
      const jobs = [
        api(`/api/devices/state?room_id=${rid}`).then(mergeLights),
        api(`/api/rooms/${rid}/state`).then(r => { S.rs = holdVolume(r); }),
      ];
      if (has('contact') || has('motion'))
        jobs.push(api(`/api/contacts/state?room_id=${rid}`).then(l => { for (const c of l) S.contact[c.id] = c; }));
      if (has('thermostat'))
        jobs.push(api(`/api/rooms/${rid}/climate`).then(l => { for (const c of l) S.clim[c.id] = c; }));
      if (has('blind'))
        jobs.push(api(`/api/blinds/state?room_id=${rid}`).then(l => { for (const b of l) S.blind[b.id] = b; }));
      if (S.scenes.length)
        jobs.push(api(`/api/rooms/${rid}/scenes`).then(l => { if (rid === S.room.id) S.scenes = l; }));
      if (has('learned'))                // angelernte Geräte (Profile aus dem Assistenten)
        jobs.push(api(`/api/learned/state?room_id=${rid}`).then(l => { for (const x of l) S.learned[x.id] = x; }));
      if (has('switch') || has('relay'))
        jobs.push(api(`/api/switches/state?room_id=${rid}`).then(l => { for (const s of l) if (holdAccept('s' + s.id, s.on == null ? null : (s.on ? 1 : 0))) S.sw[s.id] = s; }));
      await Promise.allSettled(jobs);
    } finally { polling = false; }
    if (rid === (S.room && S.room.id)) emit('state');
    if (pollAgain) poll();
  }
  // Hinweise der Bridge: welche Räume/Bereiche sich geändert haben (Daten kommen dann aus deren Zwischenspeicher)
  const ROOM_KEYS = ['lights', 'climate', 'blinds', 'contacts', 'switches', 'scenes', 'learned'];
  function listenState() {
    const es = new EventSource('/api/state/stream');
    es.onmessage = m => {
      let h; try { h = JSON.parse(m.data); } catch { return; }
      if (h.hello) { push.ok = !!h.push; return; }
      push.ok = true;
      if ((h.keys || []).includes('project')) { reloadProject(); return; }   // Composer „Refresh Navigators"
      const rid = S.room && S.room.id;
      // devs = {lights: [Raum-IDs], climate: [...]}: nur neu laden, wenn der gezeigte Raum betroffen ist
      const devHit = Object.entries(h.devs || {}).some(([k, rs]) => ROOM_KEYS.includes(k) && rs.includes(rid));
      if (h.all || devHit || (h.rooms || []).includes(rid) || (h.keys || []).some(k => ROOM_KEYS.includes(k))) poll();
      if (h.all || (h.keys || []).includes('power')) pollPower();
    };
    es.onerror = () => { push.ok = false; };
    // verborgener Tab ignoriert Hinweise → beim Zurückkehren einmal nachladen
    document.addEventListener('visibilitychange', () => { if (!document.hidden) { poll(); pollPower(); } });
  }
  const pollSoon = (ms = 800) => setTimeout(poll, ms);
  // Projekt in Composer geändert: Raumliste, Geräte, Quellen, Szenen, Buttons des aktuellen Raums neu holen
  async function reloadProject() {
    S.kodi = null;
    try {
      const rooms = await api('/api/rooms');
      S.rooms = rooms.filter(r => r.enabled && r.device_count > 0);
      allDevs = null;
      if (S.room) await loadRoom((S.rooms.find(r => r.id === S.room.id) || S.rooms[0]).id, false);
      emit('favrooms');
    } catch { /* nächster Versuch beim nächsten Signal */ }
  }
  async function pollPower() {
    try { S.power = await api('/api/rooms/power'); emit('power'); } catch { /* offline */ }
  }

  // Push vom Director (WebSocket → SSE): Temperaturen + Licht sofort
  function listen() {
    const es = new EventSource('/api/events/stream');
    es.onmessage = m => {
      let ev; try { ev = JSON.parse(m.data); } catch { return; }
      const n = ev.norm || {}, p = (ev.data && ev.data.data) || {};
      const temp = n.temperature_c ?? p.current_temperature_c;
      const bri = n.brightness ?? p.LIGHT_LEVEL;
      if (temp != null) { S.temps[ev.id] = Number(temp); emit('push', { id: ev.id, kind: 'temp' }); }
      if (bri != null) {
        const lvl = Number(bri);
        if (mergeLight(ev.id, { level: lvl, on: lvl > 0 })) emit('push', { id: ev.id, kind: 'light' });
      }
    };
  }

  // ---------------------------------------------------------------- Zielwert halten
  // Dimmer (z. B. Opus) melden während der Rampe Zwischenwerte und den Endwert teils erst nach 20–40 s.
  // Nach eigener Bedienung zeigt die Oberfläche deshalb den Zielwert, bis der Endwert ankommt.
  // Werte außerhalb des Wegs alt → neu (z. B. Wandtaster) beenden das Halten sofort.
  const held = {};
  const HOLD_LIGHT_MS = 60000, HOLD_VOL_MS = 6000;
  function holdSet(key, from, to, ms) { held[key] = { from: Number(from) || 0, to, until: Date.now() + ms }; }
  // true = Wert übernehmen, false = Zwischenwert der eigenen Änderung (ignorieren)
  function holdAccept(key, v) {
    const h = held[key];
    if (!h || v == null) return true;
    if (Date.now() > h.until || Math.abs(v - h.to) <= 2 || v < Math.min(h.from, h.to) || v > Math.max(h.from, h.to)) {
      delete held[key]; return true;
    }
    return false;
  }
  function mergeLight(id, st) {
    const lvl = st.level != null ? Number(st.level) : st.on != null ? (st.on ? 100 : 0) : null;
    if (!holdAccept('l' + id, lvl)) return false;
    S.light[id] = { ...(S.light[id] || {}), ...st };
    return true;
  }
  function mergeLights(list) { for (const s of list || []) mergeLight(s.id, s); }
  function holdVolume(r) {
    const v = r.bluos && !r.bluos.error && (S.backend || {}).volume_via !== 'c4' ? r.bluos.volume : r.volume;
    if (v == null || holdAccept('vol' + r.room_id, Number(v))) return r;
    const to = held['vol' + r.room_id].to;
    r.volume = to;
    if (r.bluos) r.bluos.volume = to;
    return r;
  }

  // ---------------------------------------------------------------- Abfragen
  // „relay" = Relais (Pumpe, Lüfter …) – in der App unter Sicherheit; „switch" = übrige Schalter (Steckdosen)
  const isRelay = d => d.type === 'switch' && d.switch_kind === 'relay';
  const ofType = (d, type) => (type.startsWith('l_') ? d.type === 'learned' && d.ltype === type.slice(2)
    : type === 'relay' ? isRelay(d) : type === 'switch' ? d.type === 'switch' && !isRelay(d) : d.type === type);
  const has = type => S.devs.some(d => ofType(d, type));
  const devsOf = type => S.devs.filter(d => ofType(d, type));
  const dev = id => S.devs.find(d => d.id === id);
  const label = d => d.note || d.name;

  function light(d) {
    const st = S.light[d.id] || {};
    const dimmable = st.dimmable !== undefined ? st.dimmable : d.function !== 'on_off';
    return { on: !!st.on, level: st.level ?? (st.on ? 100 : 0), dimmable, known: st.level != null };
  }
  // Gruppen: Positions-Befehl geht direkt an die Gruppe (wie in der App). Eine Position hat die Gruppe nur,
  // wenn alle Mitglieder gleich stehen (sonst 9999) → dann Mittelwert der Einzel-Rollläden im Raum anzeigen.
  function groupMembers(d) {
    return devsOf('blind').filter(x => x.id !== d.id && (S.blind[x.id] || {}).type !== 'Group');
  }
  function blind(d) {
    const st = S.blind[d.id] || {};
    if (st.type === 'Group' && (st.level == null || st.level === 9999)) {
      const ms = groupMembers(d).map(x => S.blind[x.id].level).filter(v => v != null);
      if (ms.length) {
        const level = Math.round(ms.reduce((a, b) => a + b, 0) / ms.length);
        return { level, known: true, open: level >= 50, moving: !!st.moving || groupMembers(d).some(x => S.blind[x.id].moving),
          group: true, hasLevel: true };
      }
    }
    const known = st.level != null && st.level !== 9999;
    return { level: known ? st.level : null, known, open: known && st.level >= 50, moving: !!st.moving,
      group: st.type === 'Group', hasLevel: !!st.has_level_cmd && known };
  }
  // Schalter (Relais, Steckdosen): on = true/false, null = Zustand unbekannt (alte WeMo)
  function sw(d) {
    const s = S.sw[d.id] || {};
    return { on: s.on === true, known: s.on != null, kind: s.kind || d.switch_kind };
  }
  function clim(d) {
    const c = S.clim[d.id] || {};
    return { temp: S.temps[d.id] ?? c.temperature_c ?? null, sp: c.heat_setpoint_c ?? null,
      mode: c.mode || '', modes: c.modes && c.modes.length ? c.modes : ['Off', 'Heat'],
      heating: !!(c.state && c.state !== 'Off'), hold: c.hold && c.hold !== 'Off' ? c.hold : '',
      humidity: c.humidity ?? null };
  }
  // Angelerntes Gerät (Profil): Zustand, Werte, Infos, Aktionen – kommt aus /api/learned/state
  const learned = d => S.learned[d.id] || { actions: [], infos: [], values: {}, state: null, active: null };
  async function learnedAct(id, action, value) {
    try { await post(`/api/learned/${id}/${action}`, value != null ? { value: String(value) } : {}); } catch (e) { toast(e.message); }
    pollSoon(1200);
  }
  // Info-Wert lesbar: Batterie true/false → niedrig/ok, Zahlen mit Einheit
  function infoText(i) {
    if (i.value == null || i.value === '') return '–';
    if (i.kind === 'battery' && (typeof i.value === 'boolean' || /^(true|false)$/i.test(String(i.value)))) return i.warn ? t('l_low') : t('l_ok');
    const n = Number(i.value);
    if (isNaN(n)) return String(i.value);                 // Text (z. B. „Unknown“) ohne Einheit
    return (Number.isInteger(n) && i.kind !== 'temperature' ? String(n) : num1(n)) + (i.unit ? ' ' + i.unit : '');
  }
  // Kontakte melden in diesem Projekt 1 = zu, 0 = offen (KNX-Logik); Bewegung 1 = erkannt
  function sec(d) {
    const st = S.contact[d.id];
    const isMotion = d.type === 'motion';
    const active = st == null ? null : isMotion ? st.state === 1 : st.state === 0;
    const n = d.name.toLowerCase();
    const icon = isMotion ? 'motion' : n.includes('tür') ? (active ? 'door_open' : 'door_closed') : (active ? 'window_open' : 'window_closed');
    const text = st == null ? t('no_status') : isMotion ? (active ? t('motion_yes') : t('motion_no')) : (active ? t('window_open') : t('window_closed'));
    return { known: st != null, active: !!active, icon, text, motion: isMotion };
  }

  // Bereiche des Raums (für Pills/Rail/Kacheln)
  const CATS = {
    light: { icon: 'light_on', types: ['light', 'l_light'] },
    blind: { icon: 'blind_open', types: ['blind', 'l_blind'] },
    thermostat: { icon: 'thermostat', types: ['thermostat', 'l_thermostat'] },
    switch: { icon: 'power', types: ['switch', 'l_switch'] },
    security: { icon: 'shield', types: ['contact', 'motion', 'relay', 'l_sensor'] },
    camera: { icon: 'camera', types: ['camera'] },
    listen: { icon: 'music', media: 'listen' },
    watch: { icon: 'tv', media: 'watch' },
    more: { icon: 'info', types: ['l_info', 'l_media'] },        // angelernte Info-/Medien-Geräte
  };
  // Kameras gelten hausweit (Türklingel!): eigene Raumkameras zuerst, dann die übrigen aus sichtbaren Räumen
  function camList() {
    const own = devsOf('camera');
    const other = Object.values(S.cams).filter(c => c.enabled !== false && !own.some(d => d.id === c.id)
      && S.rooms.some(r => r.id === c.room_id))
      .map(c => ({ id: c.id, name: c.name, note: '', type: 'camera', room: c.room, room_id: c.room_id, other: true }));
    return [...own, ...other];
  }
  const camDev = id => camList().find(c => c.id === id);
  function cats() {
    return Object.entries(CATS).filter(([k, c]) => k === 'camera' ? camList().length > 0
      : c.media ? (S.media[c.media] || []).length > 0 : c.types.some(has))
      .map(([key, c]) => ({ key, icon: c.icon }));
  }
  function summary(key) {
    if (key === 'light') {
      const n = devsOf('light').filter(d => light(d).on).length;
      return n ? t('n_on', n) : t('all_off');
    }
    if (key === 'blind') {
      const bs = devsOf('blind').map(blind).filter(b => b.known && !b.group);
      if (devsOf('blind').some(d => blind(d).moving)) return t('moving');
      if (!bs.length) return devsOf('blind').some(d => blind(d).open) ? t('open') : '–';
      const avg = Math.round(bs.reduce((a, b) => a + b.level, 0) / bs.length);
      return avg <= 2 ? t('closed') : t('pct_open', avg);
    }
    if (key === 'thermostat') {
      const c = devsOf('thermostat').map(clim).find(x => x.temp != null);
      return c ? num1(c.temp) + '°' + (c.heating ? ' · ' + t('heating') : '') : '–';
    }
    if (key === 'switch') {
      const n = devsOf('switch').filter(d => sw(d).on).length;
      return n ? t('n_on', n) : t('all_off');
    }
    if (key === 'security') {
      const list = [...devsOf('contact'), ...devsOf('motion')].map(sec);
      const open = list.filter(s => s.active && !s.motion).length + devsOf('l_sensor').filter(d => learned(d).active).length;
      return open ? t('n_open', open) : list.some(s => s.active && s.motion) ? t('motion') : t('all_closed');
    }
    if (key === 'camera') return camList().map(label)[0] || '';
    if (key === 'more') return String(devsOf('l_info').length + devsOf('l_media').length);
    if (key === 'listen' || key === 'watch') {
      const np = nowPlaying();
      return mediaOn(key) ? (np.title || np.source || sourceName(S.rs.selected_device)) : t('off');
    }
    return '';
  }
  // „Hören" ist an, sobald im Raum irgendetwas läuft – auch beim Sehen (der Ton gehört dazu, wie bei Control4);
  // „Sehen" nur, wenn eine Videoquelle gewählt ist
  function mediaOn(key) {
    const np = nowPlaying();
    if (!np.on) return false;
    return key === 'listen' ? true : (S.media.watch || []).some(s => s.id === S.rs.selected_device);
  }
  function catActive(key) {
    if (key === 'light') return devsOf('light').some(d => light(d).on);
    if (key === 'thermostat') return devsOf('thermostat').some(d => clim(d).heating);
    if (key === 'switch') return devsOf('switch').some(d => sw(d).on);
    if (key === 'security') return [...devsOf('contact'), ...devsOf('motion')].some(d => sec(d).active) || devsOf('l_sensor').some(d => learned(d).active);
    if (key === 'listen' || key === 'watch') return mediaOn(key);
    if (key === 'blind') return devsOf('blind').some(d => blind(d).moving);
    return false;
  }

  // ---------------------------------------------------------------- Medien
  const sourceName = id => [...(S.media.listen || []), ...(S.media.watch || [])].find(s => s.id === id)?.name || '';
  // Control4 springt per „nächster Titel" zum gewünschten Titel – jeder Zwischensprung kommt per Push.
  // Bis der Zieltitel läuft (höchstens 12 s), zeigt die Oberfläche gleich den Zieltitel.
  let titleHold = null;
  const holdTitle = title => { titleHold = title ? { title, until: Date.now() + 12000 } : null; };
  const normT = x => String(x || '').toLowerCase().replace(/\W+/g, '');
  function mediaTitle() {
    const real = rawTitle();
    if (titleHold) {
      if (Date.now() > titleHold.until || (real && normT(real) === normT(titleHold.title))) titleHold = null;
      else return titleHold.title;
    }
    return real;
  }
  function rawTitle() {
    const raw = S.rs.media_raw;
    if (!raw) return '';
    if (typeof raw === 'string') return raw;
    const info = raw.mediainfo || raw;
    const x = info.title || info.name || raw.$t || info.$t;
    return typeof x === 'string' ? x : '';
  }
  const BLU_SERVICE = { LocalMusic: 'bluos_lib', Capture: 'inputs' };
  function nowPlaying() {
    const b = S.rs.bluos;
    if (b && !b.error) {   // BluOS-Raum: alles vom Player
      const on = ['play', 'stream', 'pause', 'connecting'].includes(b.state);
      const src = b.service === 'Capture' ? (b.title || 'Line In') : (BLU_SERVICE[b.service] ? t(BLU_SERVICE[b.service]) : b.service);
      return { on, source: src, title: b.service === 'Capture' ? '' : b.title, artist: b.artist || '', album: b.album || '',
        playState: b.play_state || '', img: b.image || '', watch: false, bluos: true,
        shuffle: !!b.shuffle, repeat: { 0: 'all', 1: 'one', 2: 'off' }[b.repeat] || 'off', mixOk: b.service !== 'Capture',
        // Lautstärke vom Player – außer der Pegel kommt laut Composer von einem anderen Endpunkt (Verstärker, feste Player-Lautstärke)
        ...(S.backend.volume_via === 'c4' ? { volume: S.rs.volume ?? 0, muted: !!S.rs.muted } : { volume: b.volume ?? 0, muted: !!b.mute }),
        secs: b.secs || 0, total: b.total || 0, at: b.at || 0 };
    }
    const k = S.rs.kodi;
    if (k && !k.error && S.rs.selected_device && S.rs.power !== false) {   // KODI ist Quelle: Titel/Poster von KODI
      const playing = k.state && k.state !== 'stop';
      return { on: true, source: sourceName(S.rs.selected_device), title: playing ? k.title : '', artist: playing ? k.subtitle : '',
        album: '', playState: k.state || '', img: playing ? k.image : '', volume: S.rs.volume ?? 0, muted: !!S.rs.muted,
        watch: true, kodi: true, secs: k.secs || 0, total: k.live ? 0 : (k.total || 0), at: k.at || 0, res: k.resolution || '',
        shuffle: !!k.shuffle, repeat: k.repeat || 'off', mixOk: playing && !k.live };
    }
    const on = !!S.rs.selected_device && S.rs.power !== false;
    const title = mediaTitle();
    const info = (S.rs.media_raw && (S.rs.media_raw.mediainfo || S.rs.media_raw)) || {};
    // medSrcDev = eigentliche Quelle (z. B. 558 „Unsere Musik"); selected_device ist oft nur „Digital Media"
    const source = sourceName(info.medSrcDev) || sourceName(S.rs.selected_device);
    return { on, source, title, artist: typeof info.artist === 'string' ? info.artist : '',
      album: typeof info.album === 'string' ? info.album : '',
      playState: S.rs.play_state || '',      // play | pause | stop – nur bei Warteschlange (Unsere Musik)
      img: S.rs.media_img || '', volume: S.rs.volume ?? 0, muted: !!S.rs.muted,
      shuffle: !!S.rs.shuffle, repeat: S.rs.repeat ? 'all' : 'off', mixOk: false,   // Control4-Warteschlange: nur Anzeige
      watch: (S.media.watch || []).some(s => s.id === S.rs.selected_device) };
  }

  // ---------------------------------------------------------------- KODI (Mediathek – nur lesen)
  async function kodiLoad() {
    try { S.kodi = (await api('/api/kodi')).players || []; } catch { S.kodi = []; }
    return S.kodi;
  }
  // KODI dieses Raums: eine seiner Quellen steht in der Sehen-Liste; Bereiche nur, wenn der Raum sie hat
  const KODI_TABS = { movies: ['movies'], tvshows: ['tvshows'], channels: ['channels', 'recordings'], files: ['files'] };
  function kodiRoom() {
    const ids = new Set((S.media.watch || []).map(s => s.id));
    const p = (S.kodi || []).find(k => (k.ids || []).some(i => ids.has(i)));
    if (!p) return null;
    const kinds = p.sections.filter(s => KODI_TABS[s.kind] && ids.has(s.source_id)).map(s => s.kind);
    const tabs = Object.keys(KODI_TABS).filter(k => kinds.includes(k)).flatMap(k => KODI_TABS[k]);
    return tabs.length ? { did: p.driver_id, name: p.name, ok: p.ok, tabs } : null;
  }
  const kodiLib = (did, kind, params = {}) => api(`/api/kodi/${did}/library/${kind}?` + new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== '' && v != null)));
  const kodiDetails = (did, kind, id) => api(`/api/kodi/${did}/details/${kind}/${id}`);

  // Quellen-Icon vom Controller (wie in der Control4-App), sonst eigenes Symbol
  function srcIcon(s, fallback, size) {
    return s && s.icon ? `<img class="srcico" src="${esc(s.icon)}" alt="" style="width:${size}px;height:${size}px" onerror="this.outerHTML=C4.icHtml('${fallback}',${size})">`
      : ic(fallback, size);
  }
  const icHtml = (n, size) => ic(n, size);
  // Mini-Artwork (Pillen, Haus-Übersicht): Cover der Musik, sonst Icon der Quelle (TV, KODI …), sonst Symbol
  function miniArt(img, sourceId, fallback, size, cls = 'mini') {
    const fb = `this.outerHTML=C4.icHtml('${fallback}',${Math.round(size * 0.6)})`;
    if (img) return `<img class="${cls}" src="${esc(img)}" alt="" style="width:${size}px;height:${size}px" onerror="${sourceId ? `this.onerror=function(){${fb}};this.src='/api/media/srcicon/${sourceId}'` : fb}">`;
    if (sourceId) return `<img class="${cls} src" src="/api/media/srcicon/${sourceId}" alt="" style="width:${size}px;height:${size}px" onerror="${fb}">`;
    return ic(fallback, Math.round(size * 0.6));
  }
  // Cover: echte Bilder folgen, sobald das Director-Bildmuster bekannt ist → bis dahin Farbe aus dem Namen
  function hue(s) { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
  function coverStyle(artist, title) {
    const h = hue((artist || '') + (title || '')), h2 = (h + 55) % 360;
    return `background:linear-gradient(135deg,hsl(${h} 58% 52%),hsl(${h2} 62% 26%))`;
  }
  // Album-Cover über die Bridge (die holt es vom Director und speichert es zwischen)
  const coverUrl = img => (img ? '/api/media/cover/' + img : '');
  function coverHtml(artist, title, cls = 'cover', img = '') {
    const pic = img ? `<img src="${esc(img)}" alt="" loading="lazy" onerror="this.remove()">` : '';
    return `<div class="${cls}" style="${coverStyle(artist, title)}"><span class="cv-a">${esc(artist || '')}</span><span class="cv-t">${esc(title || '')}</span>${pic}</div>`;
  }

  const browseCache = {};
  async function browse(kind, artist, genre) {
    const key = [kind, artist || '', genre || ''].join('|');
    if (!browseCache[key]) {
      let url = '/api/media/browse?kind=' + kind;
      if (artist) url += '&artist=' + encodeURIComponent(artist);
      if (genre) url += '&genre=' + encodeURIComponent(genre);
      browseCache[key] = api(url).catch(e => { delete browseCache[key]; throw e; });
    }
    return browseCache[key];
  }
  const album = id => api('/api/media/albums/' + id);
  const search = q => api('/api/media/search?grouped=1&limit=12&q=' + encodeURIComponent(q));
  const recent = () => api('/api/media/recent?room_id=' + S.room.id);
  const randomAlbums = () => api('/api/media/random?n=12');
  const queue = () => api(`/api/rooms/${S.room.id}/queue`);
  // Control4-Bibliothek abspielbar? Räume mit „Digital Media" (Bridge erkennt es am Proxy) – die übrigen sind BluOS
  // Album-Wiedergabe aus „Unsere Musik": Control4 (Digital Media, Wohnzimmer) oder BluOS-Player (gleiche Sammlung)
  const canPlay = () => ['c4', 'bluos'].includes((S.backend || {}).type);
  const isBluos = () => (S.backend || {}).type === 'bluos';
  // BluOS-Raum: Control4-Quelle (Proxy-ID) → BluOS-Dienst (browseKey) bzw. Quelle ohne Funktion (Dienst fehlt am Player)
  const bluKey = id => (isBluos() && S.backend.sources ? S.backend.sources[id] : undefined);
  const bluDead = id => isBluos() && (S.backend.dead_sources || []).includes(id);

  // ---------------------------------------------------------------- BluOS-Browser (Dienste, Eingänge)
  // gemeinsamer Renderer für beide Themes: Menübaum des Players (browseKey → tiefer, play/autoplay → abspielen)
  let bluStack = [], bluItems = [], bluSeq = 0, bluEl = null;
  const bluUrl = (key, q) => `/api/bluos/${S.room.id}/browse?key=${encodeURIComponent(key || '')}${q ? '&q=' + encodeURIComponent(q) : ''}`;
  async function bluTop() {
    if (!S.bluTop) {
      try { S.bluTop = (await api(bluUrl(''))).items; } catch { S.bluTop = []; }
    }
    return S.bluTop;
  }
  const playable = it => it.autoplay || it.play || (it.action && /add-now|^audio$/.test(it.type) ? it.action : '');
  function bluItemHtml(it, i, grid) {
    const img = it.image ? `<img src="${esc(it.image)}" alt="" loading="lazy" onerror="this.remove()">` : '';
    const open = it.browse ? `C4.bluAct(${i},'open')` : playable(it) ? `C4.bluAct(${i},'play')` : '';
    const pbtn = it.browse && playable(it) ? `<button class="blu-pb" title="${t('play_album')}" onclick="event.stopPropagation();C4.bluAct(${i},'play')">${ic('play', 16)}</button>` : '';
    if (grid) return `<div class="blu-card" onclick="${open}"><div class="blu-art">${img || ic('music', 30)}${pbtn}</div><b>${esc(it.text)}</b><small>${esc(it.text2 || '')}</small></div>`;
    return `<div class="blu-row ${open ? '' : 'dis'}" onclick="${open}"><span class="blu-ic">${img || ic(it.input ? 'power' : it.browse ? 'folder_music' : 'music', 20)}</span>
      <span class="blu-tx"><b>${esc(it.text)}</b>${it.text2 ? `<small>${esc(it.text2)}</small>` : ''}</span>
      ${pbtn || (playable(it) && !it.browse ? `<span class="blu-pb">${ic('play', 16)}</span>` : it.browse ? ic('chev_right', 18) : '')}</div>`;
  }
  async function renderBlu(el, more) {
    bluEl = el = el || bluEl; if (!el || !isBluos()) return;
    const top = bluStack[bluStack.length - 1] || { key: '', title: t('services') };
    const seq = ++bluSeq;
    if (!more) { el.innerHTML = `<p class="empty">…</p>`; bluItems = []; }
    let r;
    try { r = await api(more ? bluUrl(more) : bluUrl(top.key, top.q)); }
    catch (e) { if (seq === bluSeq) el.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
    if (seq !== bluSeq) return;
    const start = bluItems.length;
    bluItems = bluItems.concat(r.items);
    const grid = r.items.filter(x => /album|playlist|artist|station/.test(x.type) && x.image).length > r.items.length / 2;
    const list = r.items.map((it, k) => bluItemHtml(it, start + k, grid)).join('');
    if (more) {
      const box = el.querySelector('.blu-list'); box.insertAdjacentHTML('beforeend', list);
      const mb = el.querySelector('.blu-more'); if (mb) mb.outerHTML = r.next ? `<button class="blu-more btn" onclick="C4.bluMore('${jsq(r.next)}')">${t('load_more')}</button>` : '';
      return;
    }
    el.innerHTML = `<div class="blu-h">${bluStack.length ? `<button class="btn blu-back" onclick="C4.bluBack()">${ic('chev_left', 16)} ${t('back')}</button>` : ''}
        <b>${esc(top.q ? t('search_res', top.q) : top.title)}</b>
        ${r.play ? `<button class="btn pri" onclick="C4.bluPlay('${jsq(r.play)}')">${ic('play', 16)} ${t('play_all')}</button>` : ''}
        ${r.search ? `<label class="blu-s">${ic('search', 16)}<input placeholder="${esc(t('search_in', r.service || top.title))}" onkeydown="if(event.key==='Enter'&&this.value.trim())C4.bluSearch('${jsq(r.search)}',this.value.trim())"></label>` : ''}</div>
      <div class="blu-list ${grid ? 'blu-grid' : ''}">${list || `<p class="empty">${t('no_hits')}</p>`}</div>
      ${r.next ? `<button class="blu-more btn" onclick="C4.bluMore('${jsq(r.next)}')">${t('load_more')}</button>` : ''}`;
  }
  function bluOpen(key, title) { bluStack.push({ key, title }); renderBlu(); }
  function bluStart(el, key, title) { bluStack = key ? [{ key, title }] : []; renderBlu(el); }
  function bluAct(i, what) {
    const it = bluItems[i]; if (!it) return;
    if (what === 'open') return bluOpen(it.browse, it.text);
    bluPlay(playable(it));
  }
  const bluBack = () => { bluStack.pop(); renderBlu(); };
  const bluMore = next => renderBlu(null, next);
  const bluSearch = (key, q) => { bluStack.push({ key, q, title: q }); renderBlu(); };
  const bluReset = () => { bluStack = []; };
  const bluPlay = url => act(async () => {
    await post(`/api/bluos/${S.room.id}/play`, { url });
    toast(t('started')); pollSoon(1200); setTimeout(pollSoon, 4000);
  });
  const queueJump = pos => act(async () => { await post(`/api/rooms/${S.room.id}/queue/${pos}`); pollSoon(1000); });
  // A–Z-Sprungmarke eines Namens („#" für Ziffern/Zeichen)
  // (gleiche Regel wie die Sortierung der Bridge: führende Satzzeichen weg, Umlaute wie Grundbuchstaben)
  const letterOf = n => { const c = String(n || '').replace(/^[^0-9A-Za-zÄÖÜäöüß]+/, '').charAt(0).toUpperCase();
    return /[A-ZÄÖÜ]/.test(c) ? c.replace('Ä', 'A').replace('Ö', 'O').replace('Ü', 'U') : '#'; };

  // Wert neben einem Schieberegler schon während des Ziehens anzeigen (Lautstärke-Zahl, „42 %“ beim Licht)
  function rangeLive(r) {
    const v = Math.round(+r.value);
    r.style.setProperty('--p', v + '%');
    let out = null;
    if (r.dataset.vol) out = r.parentElement && r.parentElement.querySelector(':scope > span:last-child, :scope > .rv');
    else if (r.dataset.lvl || r.dataset.blind) {
      const box = r.closest('[data-rbox]') || r.closest('.t, .c, .r');
      out = box && box.querySelector('[data-rv], .tx small, .cx small, .lvl');
    }
    if (out) out.textContent = r.dataset.vol ? v : r.dataset.lvl ? (v ? v + ' %' : t('off')) : v + ' %';
    rangeSend(r);
  }

  // Licht und Lautstärke schon während des Ziehens schicken (wie die Control4-App), gedrosselt auf
  // höchstens alle LIVE_MS; den Endwert schickt beim Loslassen weiterhin setLevel()/volume().
  const LIVE_MS = 300, live = {};
  function rangeSend(r) {
    const id = r.dataset.lvl ? +r.dataset.lvl : null;
    if (!id && !r.dataset.vol) return;            // Rollläden: erst beim Loslassen fahren
    const key = id ? 'l' + id : 'v' + (S.room && S.room.id);
    const L = live[key] || (live[key] = { t: 0, timer: null, sent: null });
    const fire = () => {
      L.t = Date.now(); L.timer = null;
      const v = Math.round(+r.value);
      if (v === L.sent) return;
      L.sent = v;
      if (id) {
        const prev = S.light[id] || {};
        holdSet('l' + id, prev.level ?? (prev.on ? 100 : 0), v, HOLD_LIGHT_MS);
        S.light[id] = { ...prev, level: v, on: v > 0 };
        post(`/api/devices/${id}/level`, { level: v }).catch(() => { });
      } else {
        holdSet('vol' + S.room.id, S.rs.volume, v, HOLD_VOL_MS);
        S.rs.volume = v;
        if (S.rs.bluos) S.rs.bluos.volume = v;
        post(`/api/rooms/${S.room.id}/volume`, { level: v }).catch(() => { });
      }
    };
    const wait = LIVE_MS - (Date.now() - L.t);
    if (wait <= 0) fire(); else if (!L.timer) L.timer = setTimeout(fire, wait);
  }
  // beim Loslassen: ausstehende Zwischensendung verwerfen, Endwert geht über die normale Aktion
  function rangeEnd(r) {
    const key = r.dataset.lvl ? 'l' + r.dataset.lvl : 'v' + (S.room && S.room.id);
    const L = live[key];
    if (L) { clearTimeout(L.timer); delete live[key]; }
  }

  // ---------------------------------------------------------------- Aktionen
  async function act(fn) {
    try { await fn(); } catch (e) { toast(t('err') + ': ' + e.message); }
  }
  const setLevel = (id, level) => act(async () => {
    level = Math.max(0, Math.min(100, Math.round(+level)));
    const prev = S.light[id] || {};
    holdSet('l' + id, prev.level ?? (prev.on ? 100 : 0), level, HOLD_LIGHT_MS);
    S.light[id] = { ...prev, level, on: level > 0 };
    emit('state');
    await post(`/api/devices/${id}/level`, { level });
  });
  const toggleLight = id => { const d = dev(id); return setLevel(id, d && light(d).on ? 0 : 100); };
  // Schalter: Zielzustand sofort zeigen und kurz halten (Relais melden den Endzustand erst nach dem Umschalten)
  const switchSet = (id, on) => act(async () => {
    holdSet('s' + id, on ? 0 : 1, on ? 1 : 0, 4000);
    S.sw[id] = { ...(S.sw[id] || {}), on }; emit('state');
    await post(`/api/switches/${id}/${on ? 'on' : 'off'}`); pollSoon(1500);
  });
  const switchToggle = id => { const d = dev(id); return switchSet(id, !(d && sw(d).on)); };
  // Lichtszenen: in der App ein Tipp = Szene umschalten (aktiv ↔ aus); Haus-Szenen fragen vorher nach
  const sceneToggle = id => act(async () => {
    const sc = S.scenes.find(s => s.id === id); if (!sc) return;
    if (!sc.active && !await ask(t('scene_q', sc.name), t('yes'))) return;
    sc.active = !sc.active; emit('state');
    await post(`/api/scenes/${id}/toggle`); pollSoon(1500); setTimeout(poll, 4000);
  });
  // Custom Buttons: können alles Mögliche schalten (Heizung, Geräte …) → immer kurz nachfragen
  const buttonPress = id => act(async () => {
    const b = S.buttons.flatMap(s => s.buttons.map(x => ({ ...x, screen: s.name }))).find(x => x.id === id); if (!b) return;
    if (!await ask(t('button_q', b.screen + ' · ' + b.name), t('yes'))) return;
    await post(`/api/buttons/${id}/press`); toast(t('pressed')); pollSoon(1500);
  });
  const blindDo = (id, action) => act(async () => { await post(`/api/blinds/${id}/${action}`); pollSoon(1200); });
  const blindLevel = (id, level) => act(async () => {
    await post(`/api/blinds/${id}/level`, { level: +level });
    S.blind[id] = { ...(S.blind[id] || {}), level: +level }; emit('state'); pollSoon(4000);
  });
  const blindToggle = id => { const d = dev(id); return blindDo(id, d && blind(d).open ? 'close' : 'open'); };
  const climStep = (id, delta) => act(async () => {
    const c = S.clim[id] || {};
    const val = Math.round((Number(c.heat_setpoint_c ?? 20) + delta) * 2) / 2;
    S.clim[id] = { ...c, heat_setpoint_c: val }; emit('state');
    await post(`/api/devices/${id}/climate`, { setpoint_c: val });
  });
  const climMode = (id, mode) => act(async () => {
    S.clim[id] = { ...(S.clim[id] || {}), mode }; emit('state');
    await post(`/api/devices/${id}/climate`, { mode });
  });
  const selectSource = (id, kind) => act(async () => {
    await post(`/api/rooms/${S.room.id}/select_source`, { device_id: id, kind: kind === 'watch' ? 'video' : 'audio' });
    pollSoon(); setTimeout(pollPower, 1200);
  });
  const transport = cmd => act(async () => {
    if (cmd === 'PLAY' || cmd === 'PAUSE') { S.rs.play_state = cmd === 'PLAY' ? 'play' : 'pause'; emit('state'); }
    await post(`/api/rooms/${S.room.id}/transport`, { cmd }); pollSoon();
  });
  const playPause = () => transport(S.rs.play_state === 'play' ? 'PAUSE' : 'PLAY');
  // Play/Pause-Knöpfe: ein Umschalter, wenn der Status bekannt ist, sonst beide
  // Zufall + Wiederholen (klein neben Play/Pause); Control4-Warteschlange nur Anzeige (Befehl unbekannt)
  function mixButton(size) {
    const np = nowPlaying();
    return `<button class="mx ${np.shuffle ? 'on' : ''}" ${np.mixOk ? '' : 'disabled'} title="${t('mix')}${np.mixOk ? '' : ' – ' + t('mix_na')}" onclick="C4.setShuffle()">${ic('shuffle', size)}</button>`;
  }
  function repeatButton(size) {
    const np = nowPlaying(), r = np.repeat || 'off';
    return `<button class="mx ${r !== 'off' ? 'on' : ''}" ${np.mixOk ? '' : 'disabled'} title="${t('rep_' + r)}${np.mixOk ? '' : ' – ' + t('mix_na')}" onclick="C4.setRepeat()">${ic(r === 'one' ? 'repeat_one' : 'repeat', size)}</button>`;
  }
  async function setShuffle() {
    const np = nowPlaying(), on = !np.shuffle;
    if (S.rs.bluos) S.rs.bluos.shuffle = on; else if (S.rs.kodi) S.rs.kodi.shuffle = on;
    emit('state');
    try { await post(`/api/rooms/${S.room.id}/shuffle`, { on }); } catch (e) { toast(e.message); }
    pollSoon(1000);
  }
  async function setRepeat() {
    const np = nowPlaying(), mode = { off: 'all', all: 'one', one: 'off' }[np.repeat || 'off'];
    if (S.rs.bluos) S.rs.bluos.repeat = { all: 0, one: 1, off: 2 }[mode]; else if (S.rs.kodi) S.rs.kodi.repeat = mode;
    emit('state');
    try { await post(`/api/rooms/${S.room.id}/repeat`, { mode }); } catch (e) { toast(e.message); }
    pollSoon(1000);
  }
  function ppButtons(cls, size) {
    const st = S.rs.play_state;
    if (st === 'play' || st === 'pause' || st === 'stop')
      return `<button class="${cls}" title="${st === 'play' ? 'Pause' : 'Play'}" onclick="C4.playPause()">${ic(st === 'play' ? 'pause' : 'play', size)}</button>`;
    return `<button class="${cls}" onclick="C4.transport('PLAY')">${ic('play', size)}</button><button onclick="C4.transport('PAUSE')">${ic('pause', size)}</button>`;
  }
  const volume = level => act(async () => {
    const np = nowPlaying();
    holdSet('vol' + S.room.id, np.volume, +level, HOLD_VOL_MS);
    S.rs.volume = +level;
    if (S.rs.bluos) S.rs.bluos.volume = +level;     // BluOS-Räume zeigen den Player-Wert → mitziehen, sonst springt der Regler zurück
    emit('state');
    await post(`/api/rooms/${S.room.id}/volume`, { level: +level }); pollSoon();
  });
  const volumeStep = delta => act(async () => { await post(`/api/rooms/${S.room.id}/volume`, { delta }); pollSoon(500); });
  const mute = onoff => act(async () => { await post(`/api/rooms/${S.room.id}/mute`, { on: onoff }); pollSoon(500); });
  // Raum aus: vorher kurz nachfragen (schaltet Musik/TV und Verstärker des ganzen Raums ab)
  const roomOff = async () => {
    if (!await ask(t('room_off_q', S.room.name), t('room_off'))) return;
    act(async () => { await post(`/api/rooms/${S.room.id}/off`); pollSoon(); setTimeout(pollPower, 1200); });
  };
  // track = Position im Album; songId (bevorzugt) = Titel-ID, die Bridge rechnet die Position selbst (Mehrfach-CDs)
  const playAlbum = (id, track, songId) => act(async () => {
    const r = await post('/api/media/play', { room_id: S.room.id, album_id: id, ...(songId ? { song_id: songId } : track > 1 ? { track } : {}) });
    holdTitle(r && r.title);
    toast(r && r.title ? t('track_started_name', r.title) : track > 1 ? t('track_started', track) : t('album_started'));
    pollSoon(1500); setTimeout(pollPower, 2000);
  });
  // Nur diesen Titel (BluOS); Control4 kennt keinen Einzeltitel → dort Album ab diesem Titel
  const playSong = (id, track, songId) => act(async () => {
    const r = await post('/api/media/play', { room_id: S.room.id, album_id: id, single: true, ...(songId ? { song_id: songId } : { track: track || 1 }) });
    holdTitle(r && r.title);
    toast(r && r.single ? t('song_started') : t('song_c4', track || 1)); pollSoon(1500); setTimeout(pollPower, 2000);
  });

  // ---------------------------------------------------------------- Fernbedienung (Sehen)
  // Tasten mit data-rk="up|ok|…" – Pfeile/Kanal/Lautstärke wiederholen beim Gedrückthalten.
  const REPEAT = new Set(['up', 'down', 'left', 'right', 'ch_up', 'ch_down', 'vol_up', 'vol_down', 'page_up', 'page_down']);
  function remote(key) {
    if (navigator.vibrate) { try { navigator.vibrate(8); } catch { /* egal */ } }
    if (key === 'vol_up' || key === 'vol_down') return volumeStep(key === 'vol_up' ? 2 : -2);
    if (key === 'mute') return mute(!S.rs.muted);
    if (key === 'off') return roomOff();
    return act(() => post(`/api/rooms/${S.room.id}/remote`, { key }));
  }
  let rkTimer = null, rkWait = null, rkBtn = null;
  const rkStop = () => {
    clearTimeout(rkWait); clearInterval(rkTimer); rkWait = rkTimer = null;
    if (rkBtn) rkBtn.classList.remove('down'); rkBtn = null;
  };
  document.addEventListener('pointerdown', e => {
    const b = e.target.closest && e.target.closest('[data-rk]'); if (!b) return;
    e.preventDefault(); rkStop();
    const k = b.dataset.rk; rkBtn = b; b.classList.add('down');
    remote(k);
    if (REPEAT.has(k)) rkWait = setTimeout(() => { rkTimer = setInterval(() => remote(k), 280); }, 450);
  });
  document.addEventListener('pointerup', rkStop, true);
  document.addEventListener('pointercancel', rkStop, true);
  document.addEventListener('pointerout', e => { if (rkBtn && e.target === rkBtn && !rkBtn.contains(e.relatedTarget)) rkStop(); }, true);
  // Tastatur (Wandtablet mit Tastatur / PC): Pfeile, Enter, Backspace, Ziffern – nur wenn die Fernbedienung sichtbar ist
  document.addEventListener('keydown', e => {
    if (!document.querySelector('.rmt') || (e.target && e.target.matches && e.target.matches('input, textarea, select'))) return;
    const k = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right', Enter: 'ok',
      Backspace: 'back', Escape: 'back', ' ': 'pause', PageUp: 'ch_up', PageDown: 'ch_down' }[e.key] || (/^[0-9]$/.test(e.key) ? e.key : null);
    if (k) { e.preventDefault(); remote(k); }
  });
  // Gemeinsames Markup der Fernbedienung (Themes gestalten es per CSS, .rmt)
  // Aufbau wie in der Control4-App: Reiter „Steuerelemente" / „Tastenfeld"
  let rTab = 'ctl';
  document.addEventListener('click', e => {
    const b = e.target.closest && e.target.closest('[data-rt]'); if (!b) return;
    rTab = b.dataset.rt;
    $$('.rmt').forEach(m => {
      m.querySelectorAll('[data-rt]').forEach(x => x.classList.toggle('on', x.dataset.rt === rTab));
      m.querySelectorAll('[data-rp]').forEach(p => { p.hidden = p.dataset.rp !== rTab; });
    });
  });
  function remoteHtml() {
    const b = (k, inner, cls = '', title = '') => `<button class="rk ${cls}" data-rk="${k}" title="${esc(title || k)}">${inner}</button>`;
    const vol = nowPlaying().volume ?? 0;
    const nums = [1, 2, 3, 4, 5, 6, 7, 8, 9].map(n => b(String(n), n)).join('') + b('star', '*') + b('0', '0') + b('pound', '#');
    return `<div class="rmt">
      <div class="rtabs"><button data-rt="ctl" class="${rTab === 'ctl' ? 'on' : ''}">${t('r_ctl')}</button><button data-rt="num" class="${rTab === 'num' ? 'on' : ''}">${t('r_num')}</button>
        ${b('home', ic('home', 20), '', 'Home')}${b('off', ic('power', 20), 'pw', t('room_off'))}</div>
      <div data-rp="ctl" ${rTab === 'ctl' ? '' : 'hidden'}>
        <div class="rbar rtop">${b('menu', t('r_menu'))}${b('cancel', t('r_cancel'))}${b('info', t('r_info'))}${b('back', t('r_back'))}</div>
        <div class="rmid">
          <div class="rock">${b('vol_up', '+', '', t('volume') + ' +')}<small>${t('r_vol')}</small>${b('vol_down', '−', '', t('volume') + ' −')}</div>
          <div class="dpad">${b('up', '▲', 'du')}${b('left', '◀', 'dl')}${b('right', '▶', 'dr')}${b('down', '▼', 'dd')}${b('ok', t('r_ok'), 'ok')}</div>
          <div class="rock">${b('ch_up', '+', '', t('channel') + ' +')}<small>${t('r_ch')}</small>${b('ch_down', '−', '', t('channel') + ' −')}</div>
        </div>
        <div class="rbar rgrid">${b('rew', ic('rew', 24))}${b('guide', t('r_guide'))}${b('ffwd', ic('ffwd', 24))}
          ${b('prev', ic('prev', 22))}${b('play', ic('play', 26))}${b('next', ic('next', 22))}
          ${b('rec', ic('rec', 24), 'recb')}${b('pause', ic('pause', 26))}${b('stop', ic('stop', 22))}</div>
        <div class="rbar rcol">${b('mute', ic(S.rs.muted ? 'vol_mute' : 'volume', 22), S.rs.muted ? 'on' : '', t('mute'))}${b('red', '<i></i>', 'cr')}${b('green', '<i></i>', 'cg')}${b('yellow', '<i></i>', 'cy')}${b('blue', '<i></i>', 'cb')}</div>
        <div class="rvol"><input type="range" class="rng" min="0" max="100" value="${vol}" style="--p:${vol}%" data-vol="1"><span class="rv">${vol}</span></div>
      </div>
      <div data-rp="num" ${rTab === 'num' ? '' : 'hidden'}>
        <div class="rnum">${nums}</div>${b('ok', t('r_enter'), 'renter')}
      </div></div>`;
  }
  // Wiederkehrende Aktualisierung ohne Neuaufbau (Stumm-Taste, Lautstärke – nicht während des Ziehens)
  let rngHeld = null;
  document.addEventListener('pointerdown', e => { if (e.target.matches && e.target.matches('.rng')) rngHeld = e.target; }, true);
  document.addEventListener('pointerup', () => setTimeout(() => { rngHeld = null; }, 400), true);
  function remoteSync() {
    const m = document.querySelector('.rmt [data-rk="mute"]');
    if (m) { m.classList.toggle('on', !!S.rs.muted); m.innerHTML = ic(S.rs.muted ? 'vol_mute' : 'volume', 22); }
    const r = document.querySelector('.rmt .rvol .rng');
    if (r && rngHeld !== r) {
      const v = nowPlaying().volume ?? 0;
      r.value = v; r.style.setProperty('--p', v + '%'); r.nextElementSibling.textContent = v;
    }
  }

  // ---------------------------------------------------------------- Favoriten (pro Benutzer + Raum)
  // Einträge: Geräte-ID (Zahl) | 'media' | 'security' | 'cat:<bereich>'
  function okFav(f) {
    if (typeof f === 'number') return !!(dev(f) || camDev(f));
    if (f === 'media') return (S.media.listen || []).length > 0;
    if (f === 'security') return has('contact') || has('motion');
    if (typeof f === 'string' && f.startsWith('cat:')) return cats().some(c => c.key === f.slice(4));
    return false;
  }
  function defaultFavs() {
    if (theme === 'os3') return cats().map(c => 'cat:' + c.key);
    const f = [];
    const firstDim = devsOf('light').find(d => light(d).dimmable) || devsOf('light')[0];
    if (firstDim) f.push(firstDim.id);
    devsOf('light').filter(d => d !== firstDim).slice(0, 3).forEach(d => f.push(d.id));
    if (has('thermostat')) f.push(devsOf('thermostat')[0].id);
    const bl = devsOf('blind').find(d => blind(d).group) || devsOf('blind')[0];
    if (bl) f.push(bl.id);
    if ((S.media.listen || []).length) f.push('media');
    if (has('contact') || has('motion')) f.push('security');
    if (has('camera')) f.push(devsOf('camera')[0].id);
    return f;
  }
  function favList() {
    const saved = S.layout.rooms[S.room.id];
    return (saved ? saved : defaultFavs()).filter(okFav);
  }
  const isFav = f => favList().includes(f);
  async function saveFavs(list, scope) {
    S.layout.rooms[S.room.id] = list;
    emit('layout');
    try { await put('/api/ui/layout', { rooms: S.layout.rooms, sizes: S.layout.sizes || {}, ...(scope ? { scope } : {}) }); }
    catch (e) { toast(t('err') + ': ' + e.message); }
  }
  function addFav(f) {
    const l = favList();
    if (l.includes(f)) { toast(t('fav_have')); return; }
    saveFavs([...l, f]); toast(t('fav_added'));
  }
  const setFavOrder = l => saveFavs(l.filter(okFav));
  const rmFav = f => { const z = (S.layout.sizes || {})[S.room.id]; if (z) delete z[f]; return saveFavs(favList().filter(x => x !== f)); };
  // X4: Kachelgröße in Rasterzellen + Geräte einer Gruppen-Kachel (gespeichert wie die Favoriten)
  const favSize = f => ((S.layout.sizes || {})[S.room.id] || {})[f] || null;
  function setFavSize(f, v) {
    const all = S.layout.sizes = S.layout.sizes || {}, z = all[S.room.id] = all[S.room.id] || {};
    if (v) z[f] = { ...(z[f] || {}), ...v }; else delete z[f];
    return saveFavs(favList());
  }
  const toggleFav = f => (isFav(f) ? rmFav(f) : addFav(f));
  function moveFav(f, dir) {
    const l = favList(), i = l.indexOf(f), j = i + dir;
    if (i < 0 || j < 0 || j >= l.length) return;
    [l[i], l[j]] = [l[j], l[i]];
    saveFavs(l);
  }
  async function saveAsDefault() { await saveFavs(favList(), 'default'); toast(t('layout_default_saved')); }
  async function resetLayout() {
    await del('/api/ui/layout').catch(() => { });
    S.layout = await api('/api/ui/layout').catch(() => ({ rooms: {} }));
    S.layout.sizes = S.layout.sizes || {};
    emit('layout');
  }
  function setEditing(v) { S.editing = v; document.body.classList.toggle('editing', v); if (v) toast(t('edit_hint')); emit('layout'); }

  // Auswahl-Dialog „zur Startseite hinzufügen" (gleich für alle Themes, Optik über Theme-Tokens)
  function showPicker() {
    closePicker();
    const favs = favList();
    const catItems = cats().map(c => ({ f: 'cat:' + c.key, icon: c.icon, name: t(theme === 'os3' && c.key === 'light' ? 'os3_light' : 'cat_' + c.key) }));
    if ((S.media.listen || []).length) catItems.push({ f: 'media', icon: 'music', name: t('now_playing') });
    const devItems = S.devs.filter(d => ['light', 'blind', 'thermostat', 'switch', 'camera'].includes(d.type))
      .map(d => ({ f: d.id, icon: { light: 'light_on', blind: 'blind_open', thermostat: 'thermostat', switch: 'power', camera: 'camera' }[d.type], name: label(d) }))
      .concat(camList().filter(c => c.other).map(c => ({ f: c.id, icon: 'camera', name: `${c.name} (${c.room})` })));
    const row = it => `<button class="pk-row ${favs.includes(it.f) ? 'have' : ''}" onclick="C4.pickerAdd(${typeof it.f === 'number' ? it.f : `'${it.f}'`}, this)">
        <span class="pk-ic">${ic(it.icon, 20)}</span><span>${esc(it.name)}</span><span class="pk-st">${favs.includes(it.f) ? '✓' : '＋'}</span></button>`;
    const el = document.createElement('div');
    el.id = 'c4picker';
    el.innerHTML = `<div class="pk-box glass"><div class="pk-h"><b>${t('pick_title')}</b><span>${esc(S.room.name)}</span>
        <button class="pk-x" onclick="C4.closePicker()">${ic('x', 18)}</button></div>
      <div class="pk-body"><div class="pk-sec">${t('pick_cats')}</div>${catItems.map(row).join('')}
      <div class="pk-sec">${t('pick_devs')}</div>${devItems.map(row).join('')}</div></div>`;
    el.addEventListener('click', e => { if (e.target === el) closePicker(); });
    document.body.appendChild(el);
  }
  function pickerAdd(f, btn) {
    if (isFav(f)) return;
    saveFavs([...favList(), f]);
    btn.classList.add('have'); btn.querySelector('.pk-st').textContent = '✓';
  }
  const closePicker = () => { const p = $('#c4picker'); if (p) p.remove(); };

  // ---------------------------------------------------------------- Kameras
  const snapUrl = id => `/api/cameras/${id}/snapshot?ts=${Date.now()}`;
  const isLive = id => S.cams[id] && S.cams[id].mode === 'rtsp' && S.cams[id].live && S.cams[id].live.whep;
  async function attachLive(video, id) {
    try {
      const pc = new RTCPeerConnection();
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });
      pc.ontrack = e => { video.srcObject = e.streams[0]; video.play().catch(() => { }); };
      await pc.setLocalDescription(await pc.createOffer());
      await new Promise(res => {
        if (pc.iceGatheringState === 'complete') return res();
        pc.addEventListener('icegatheringstatechange', () => pc.iceGatheringState === 'complete' && res());
        setTimeout(res, 1500);
      });
      const r = await fetch(`/hls/${id}/whep`, { method: 'POST', headers: { 'Content-Type': 'application/sdp' }, body: pc.localDescription.sdp });
      if (!r.ok) throw new Error('whep ' + r.status);
      await pc.setRemoteDescription({ type: 'answer', sdp: await r.text() });
      video._pc = pc;
      // WebRTC-Video läuft per UDP direkt zu MediaMTX – im LAN blockiert das oft die Firewall (Bild bleibt
      // schwarz). Kommt nach 6 s kein Bild, auf HLS über die Bridge umschalten (normales HTTP).
      let tries = 0;
      const check = () => {                      // (Element kann beim Neuzeichnen kurz ausgehängt sein)
        if (video._dead || video.videoWidth) return;
        if (!video.isConnected) { if (++tries < 30) setTimeout(check, 2000); return; }
        pc.close(); attachHls(video, id);
      };
      setTimeout(check, 6000);
    } catch (e) {
      attachHls(video, id);
    }
  }
  let hlsLib = null;
  function attachHls(video, id) {
    video._pc = video._pc || true;
    const src = `/hls/${id}/index.m3u8`;
    const fail = () => {   // letzter Ausweg: Standbild (wird alle 10 s erneuert)
      const img = Object.assign(document.createElement('img'), { className: 'cam-media', src: snapUrl(id), alt: '' });
      img.dataset.snap = id;
      img.onerror = () => img.replaceWith(Object.assign(document.createElement('div'), { className: 'cam-err', textContent: t('live_err') }));
      video._dead = true;
      video.replaceWith(img);
    };
    // Vorrang hls.js: neuere Chrome melden zwar natives HLS („maybe"), spielen MediaMTX' MPEG-TS aber nicht ab.
    // Natives HLS nur, wo hls.js nicht läuft (iPhone/iPad-Safari).
    const native = () => {
      if (!video.canPlayType('application/vnd.apple.mpegurl')) return fail();
      video.src = src; video.onerror = fail; video.play().catch(() => { });
    };
    hlsLib = hlsLib || new Promise((res, rej) => {
      const s = document.createElement('script'); s.src = '/vendor/hls.min.js'; s.onload = () => res(window.Hls); s.onerror = rej;
      document.head.appendChild(s);
    });
    hlsLib.then(Hls => {
      if (!Hls || !Hls.isSupported()) return native();
      const h = new Hls({ liveSyncDurationCount: 2, lowLatencyMode: true });
      h.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) { h.destroy(); fail(); } });
      h.loadSource(src); h.attachMedia(video);
      video.play().catch(() => { });
      video._hls = h;
    }).catch(native);
  }
  // Kamera-Element: Live-Video (RTSP/WHEP) oder Standbild; nach dem Einfügen C4.wireCams(root) aufrufen
  function camHtml(d) {
    return isLive(d.id)
      ? `<video class="cam-media" data-live="${d.id}" autoplay muted playsinline></video>`
      : `<img class="cam-media" data-snap="${d.id}" src="${snapUrl(d.id)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'cam-err',textContent:C4.t('cam_err')}))">`;
  }
  // Laufende Livebilder überleben das Neuzeichnen (Themes rendern alle 5 s neu): das alte, verbundene
  // Video-Element wird statt des neuen wieder eingesetzt – sonst startet WebRTC ständig von vorn.
  const liveEls = {};
  function wireCams(root = document) {
    $$('video[data-live]', root).forEach(v => {
      const id = +v.dataset.live, old = liveEls[id];
      if (old && old !== v && !old.isConnected && !old._dead && (old._pc || old._hls)) {
        old.className = v.className;
        v.replaceWith(old);
        old.play().catch(() => { });
        return;
      }
      if (!v._pc) { liveEls[id] = v; attachLive(v, id); }
    });
  }
  setInterval(() => { if (!document.hidden) $$('img[data-snap]').forEach(i => { i.src = snapUrl(i.dataset.snap); }); }, 10000);

  // ---------------------------------------------------------------- Kleinkram
  // Kleine Ja/Nein-Abfrage (Enter = ja, Esc/Klick daneben = nein) → Promise<boolean>
  function ask(text, yes) {
    return new Promise(done => {
      const el = document.createElement('div');
      el.id = 'c4ask';
      el.innerHTML = `<div class="ak-box glass"><p>${esc(text)}</p><div class="ak-b">
        <button class="ak-no">${t('no')}</button><button class="ak-yes">${esc(yes || t('yes'))}</button></div></div>`;
      const end = v => { el.remove(); document.removeEventListener('keydown', key); done(v); };
      const key = e => { if (e.key === 'Escape') end(false); if (e.key === 'Enter') end(true); };
      el.addEventListener('click', e => { if (e.target === el) end(false); });
      el.querySelector('.ak-no').onclick = () => end(false);
      el.querySelector('.ak-yes').onclick = () => end(true);
      document.addEventListener('keydown', key);
      document.body.appendChild(el);
      el.querySelector('.ak-yes').focus();
    });
  }
  function toast(msg) {
    let el = $('#c4toast');
    if (!el) { el = document.createElement('div'); el.id = 'c4toast'; document.body.appendChild(el); }
    el.textContent = msg; el.classList.add('show');
    clearTimeout(el._h); el._h = setTimeout(() => el.classList.remove('show'), 2600);
  }
  // Lieblingsräume: pro Benutzer (Stern in der Raumliste); ohne eigene Wahl gilt der alte Hausstandard (Admin)
  const favRooms = () => (Array.isArray(S.prefs.fav_rooms) ? S.prefs.fav_rooms : S.rooms.filter(r => r.favorite).map(r => r.id));
  const isFavRoom = id => favRooms().includes(id);
  function toggleFavRoom(id) {
    const on = !isFavRoom(id), list = favRooms().filter(x => x !== id);
    if (on) list.push(id);
    setPref('fav_rooms', list);
    toast(on ? t('fav_room_on') : t('fav_room_off'));
    emit('favrooms');
  }
  const favStar = id => `<span class="rstar ${isFavRoom(id) ? 'on' : ''}" role="button" title="${t('fav_room')}"
    onclick="event.stopPropagation();C4.toggleFavRoom(${id})">${ic('star', 18)}</span>`;
  function roomGroups() {
    const groups = [];
    const favs = favRooms().map(id => S.rooms.find(r => r.id === id)).filter(Boolean);
    if (favs.length) groups.push({ name: t('favorites'), rooms: favs, fav: true });
    for (const r of S.rooms) {
      const name = r.floor || t('other_floor');
      let g = groups.find(x => !x.fav && x.name === name);
      if (!g) { g = { name, rooms: [] }; groups.push(g); }
      g.rooms.push(r);
    }
    return groups;
  }
  // Haus-Übersicht: Lichter an je Raum (ein Batch-Aufruf) + Medien aus S.power
  let allDevs = null;
  async function house() {
    if (!allDevs) allDevs = await api('/api/devices?enabled_only=true').catch(() => []);
    const [states, playing] = await Promise.all([api('/api/devices/state').catch(() => []), api('/api/rooms/playing').catch(() => ({}))]);
    const onIds = new Set(states.filter(s => s.on).map(s => s.id));
    return S.rooms.map(r => {
      const ds = allDevs.filter(d => d.room_id === r.id);
      const th = ds.find(d => d.type === 'thermostat' && S.temps[d.id] != null);
      return { room: r, lightsOn: ds.filter(d => d.type === 'light' && onIds.has(d.id)).length,
        media: !!S.power[r.id], play: playing[r.id] || null, temp: th ? S.temps[th.id] : null };
    });
  }

  // ---------------------------------------------------------------- Einrichtungsassistent
  function showSetup(configured) {
    const d = document.createElement('div');
    d.id = 'c4setup';
    d.innerHTML = `<div class="su-box glass">
      <h2>${t('wiz_welcome')}</h2>
      <div class="su-lang"><button onclick="C4.setLang('de').then(()=>{this.closest('#c4setup').remove();C4.showSetup(${configured})})">Deutsch</button>
        <button onclick="C4.setLang('en').then(()=>{this.closest('#c4setup').remove();C4.showSetup(${configured})})">English</button></div>
      <p>${configured ? t('wiz_noproj') : t('wiz_cfg')}</p>
      <input id="su-user" placeholder="${t('c4_email')}" autocomplete="off">
      <input id="su-pass" type="password" placeholder="${t('c4_pass')}" autocomplete="new-password">
      <div class="su-row"><input id="su-ip" placeholder="${t('c4_ip')}"><button id="su-test">${t('test')}</button></div>
      <div class="su-row"><input id="su-place" placeholder="${t('wiz_place')}" autocomplete="off"><button id="su-geo">${t('wiz_find')}</button></div>
      <div id="su-geo-list" class="su-geo"></div>
      <div id="su-mtx" class="su-mtx"></div>
      <div id="su-msg"></div>
      <button id="su-go" class="su-pri" disabled>${t('save_scan')}</button></div>`;
    document.body.appendChild(d);
    const msg = (txt, col) => { const m = $('#su-msg'); m.textContent = txt; m.style.color = col || ''; };
    $('#su-test').onclick = async () => {
      msg(t('testing'));
      try {
        const r = await post('/api/setup/test', { c4_user: $('#su-user').value.trim(), c4_password: $('#su-pass').value, c4_ip: $('#su-ip').value.trim() || null });
        if (r.ok) { msg(`${t('wiz_ok')} ${r.controller || ''}`, 'var(--ok)'); $('#su-go').disabled = false; }
        else msg(`${t('wiz_fail')} ${r.error || ''}`, 'var(--err)');
      } catch (e) { msg(e.message, 'var(--err)'); }
    };
    // Wetter-Ort gleich mit festlegen (Open-Meteo-Ortssuche, optional)
    let place = null;
    const geo = async () => {
      const q = $('#su-place').value.trim(), box = $('#su-geo-list');
      if (!q) { place = null; box.innerHTML = ''; return; }
      box.textContent = '…';
      try {
        const hits = await api('/api/assist/geocode?name=' + encodeURIComponent(q));
        box.innerHTML = hits.length ? hits.map((h, i) => `<button data-i="${i}">${esc(h.name)} <small>${esc([h.admin, h.country].filter(Boolean).join(', '))}</small></button>`).join('')
          : `<span>${t('wiz_noplace')}</span>`;
        box.querySelectorAll('button').forEach(b => b.onclick = () => {
          place = hits[+b.dataset.i]; $('#su-place').value = place.name;
          box.innerHTML = `<span style="color:var(--ok)">✓ ${esc(place.name)}, ${esc(place.admin || place.country || '')}</span>`;
        });
      } catch (e) { box.textContent = e.message; }
    };
    // Kamera-Livestream: MediaMTX passend zum Betriebssystem anbieten (Windows liegt bei, Linux/macOS laden)
    const mtx = async (load) => {
      const box = $('#su-mtx');
      try {
        if (load) box.innerHTML = `<span>${t('mtx_loading')}</span>`;
        const m = load ? await post('/api/setup/mediamtx') : await api('/api/setup/mediamtx');
        box.innerHTML = m.installed ? `<span style="color:var(--ok)">✓ ${t('mtx_ok', m.installed_version || m.version, m.label)}</span>`
          : m.supported ? `<span>${t('mtx_q', m.label)}</span><button id="su-mtx-go">${t('mtx_load')}</button>`
          : `<span>${t('mtx_none', m.label)}</span>`;
        const b = $('#su-mtx-go'); if (b) b.onclick = () => { b.disabled = true; mtx(true); };
      } catch (e) { box.innerHTML = `<span style="color:var(--err)">${esc(e.message)}</span>`; }
    };
    mtx(false);
    $('#su-geo').onclick = geo;
    $('#su-place').onkeydown = e => { if (e.key === 'Enter') geo(); };
    $('#su-go').onclick = async () => {
      msg(t('wiz_saving'));
      // Sprache des Sprachassistenten = Oberflächensprache, solange im Admin noch keine gewählt ist
      try { await put('/api/voice/config', { lang_if_unset: lang }); } catch { /* später im Admin */ }
      if (place) {
        try { await put('/api/voice/config', { assist: { weather: { enabled: true, place: place.name, lat: place.lat, lon: place.lon } } }); }
        catch { /* Ort lässt sich später im Admin setzen */ }
      }
      const body = { c4_user: $('#su-user').value.trim(), c4_ip: $('#su-ip').value.trim() };
      if ($('#su-pass').value) body.c4_password = $('#su-pass').value;
      try {
        await put('/api/config', body);
        const r = await post('/api/scan');
        msg(`✓ ${r.rooms}/${r.devices} ${t('wiz_imported')}`, 'var(--ok)');
        setTimeout(() => location.reload(), 1200);
      } catch (e) { msg(e.message, 'var(--err)'); }
    };
  }

  return {
    S, on, emit, t, num1, fmtTime, fmtDate, greeting, start, api, post, put,
    get lang() { return lang; }, setLang, setTheme, setPref, logout,
    loadRoom, poll, has, devsOf, dev, label, light, blind, clim, sec, sw, switchSet, switchToggle, sceneToggle, buttonPress, cats, summary, catActive,
    nowPlaying, sourceName, coverHtml, coverStyle, coverUrl, browse, album, search, recent, randomAlbums, queue,
    canPlay, isBluos, bluKey, bluDead, bluTop, renderBlu, bluStart, bluOpen, bluAct, bluBack, bluMore, bluSearch, bluPlay, bluReset, queueJump,
    letterOf, srcIcon, icHtml, miniArt, kodiLoad, kodiRoom, kodiLib, kodiDetails, albView, albViewBtn, toggleAlbView, remote, remoteHtml, remoteSync, camList, camDev, isFavRoom, toggleFavRoom, favStar,
    learned, learnedAct, infoText, setLevel, toggleLight, mergeLights, rangeLive, rangeEnd, blindDo, blindLevel, blindToggle, climStep, climMode, selectSource,
    transport, playPause, ppButtons, mixButton, repeatButton, setShuffle, setRepeat, volume, volumeStep, mute, roomOff, playAlbum, playSong,
    favList, isFav, addFav, rmFav, toggleFav, moveFav, favSize, setFavSize, setFavOrder, saveAsDefault, resetLayout, setEditing,
    showPicker, pickerAdd, closePicker, camHtml, wireCams, snapUrl, toast, roomGroups, house, showSetup,
    get theme() { return theme; },
  };
})();
