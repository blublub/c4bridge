/* c4bridge-Icon-Satz (24x24, Strichzeichnung, currentColor).
   Nutzung: ic('light_on') liefert ein komplettes Inline-SVG.
   Alle Seiten (app.html mit X4/OS 3, admin, login) binden diese Datei ein. */
const ICONS = {
  // Licht --------------------------------------------------------------------
  light_on: '<path d="M12 3.2a5.8 5.8 0 0 0-3.7 10.2c.65.55 1 1.3 1 2.1h5.4c0-.8.35-1.55 1-2.1A5.8 5.8 0 0 0 12 3.2z"/>'
    + '<path d="M9.8 18.7h4.4M10.7 20.9h2.6"/>'
    + '<path d="M12 .9v1.6M4.3 4.1l1.2 1.2M19.7 4.1l-1.2 1.2M2.1 12.1h1.6M20.3 12.1h1.6M4.3 20.1l1.2-1.2M19.7 20.1l-1.2-1.2"/>',
  light_off: '<path d="M12 3.2a5.8 5.8 0 0 0-3.7 10.2c.65.55 1 1.3 1 2.1h5.4c0-.8.35-1.55 1-2.1A5.8 5.8 0 0 0 12 3.2z"/>'
    + '<path d="M9.8 18.7h4.4M10.7 20.9h2.6"/>',
  // Rollladen / Jalousie -------------------------------------------------------
  blind_open: '<path d="M4.5 3h15"/><path d="M5.5 4.5h13v15.5h-13z"/>'
    + '<path d="M5.5 4.5h13v4.4h-13z"/><path d="M5.5 6.1h13M5.5 7.7h13"/><path d="M12 8.9v2.6"/>',
  blind_closed: '<path d="M4.5 3h15"/><path d="M5.5 4.5h13v15.5h-13z"/>'
    + '<path d="M5.5 7.4h13M5.5 9.9h13M5.5 12.4h13M5.5 14.9h13M5.5 17.4h13"/>',
  // Fenster --------------------------------------------------------------------
  window_closed: '<path d="M4.5 4.5h15v15h-15z"/><path d="M12 4.5v15M4.5 12h15"/>',
  window_open: '<path d="M4.5 4.5h15v15h-15z"/>'
    + '<path d="M12 5.6 6.4 8.2v7.6L12 18.4zM12 5.6l5.6 2.6v7.6L12 18.4z"/>',
  // Tür ------------------------------------------------------------------------
  door_closed: '<path d="M6.5 3.5h11v17h-11z"/><circle cx="14.7" cy="12" r="1" fill="currentColor" stroke="none"/>',
  door_open: '<path d="M5 3.5h14v17H5z"/><path d="M17 20.5 12 18V4.8l5 1.7z"/>'
    + '<circle cx="13.3" cy="12" r=".9" fill="currentColor" stroke="none"/>',
  // Bewegung --------------------------------------------------------------------
  motion: '<circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none"/>'
    + '<path d="M7 7a7 7 0 0 0 0 10M17 7a7 7 0 0 1 0 10"/>'
    + '<path d="M4.4 4.4a10.8 10.8 0 0 0 0 15.2M19.6 4.4a10.8 10.8 0 0 1 0 15.2"/>',
  // Kategorien --------------------------------------------------------------------
  thermostat: '<path d="M12 3.5a2.2 2.2 0 0 1 2.2 2.2v8.2a4.3 4.3 0 1 1-4.4 0V5.7A2.2 2.2 0 0 1 12 3.5z"/>'
    + '<path d="M12 9.5V15"/><circle cx="12" cy="17" r="1.3" fill="currentColor" stroke="none"/>',
  shield: '<path d="M12 3l7 3v5c0 4.8-3 8.4-7 10-4-1.6-7-5.2-7-10V6z"/>',
  camera: '<path d="M3.5 7.5h11v9h-11z"/><path d="M14.5 12l6-3.5v7z"/>',
  music: '<path d="M9 18.5V6l10-2v12.5"/>'
    + '<circle cx="6.8" cy="18.5" r="2.2" fill="currentColor" stroke="none"/>'
    + '<circle cx="16.8" cy="16.5" r="2.2" fill="currentColor" stroke="none"/>',
  tv: '<path d="M3.5 6h17v11h-17z"/><path d="M8 20.5h8M12 17v3.5"/>',
  home: '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/><path d="M10 20v-6h4v6"/>',
  gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
    + '<circle cx="12" cy="12" r="3"/>',
  // Aktionen ----------------------------------------------------------------------
  pencil: '<path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 0 1 3 3L8 19z"/><path d="M14.5 6.5l3 3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
  drag: '<circle cx="9" cy="6" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="6" r="1.2" fill="currentColor" stroke="none"/>'
    + '<circle cx="9" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="1.2" fill="currentColor" stroke="none"/>'
    + '<circle cx="9" cy="18" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="18" r="1.2" fill="currentColor" stroke="none"/>',
  chev_down: '<path d="M6 9l6 6 6-6"/>',
  chev_up: '<path d="M6 15l6-6 6 6"/>',
  arrow_up: '<path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/>',
  arrow_down: '<path d="M12 5v14M5.5 12.5 12 19l6.5-6.5"/>',
  power: '<path d="M12 3v8"/><path d="M7 6.2a7.5 7.5 0 1 0 10 0"/>',
  star: '<path d="M12 3.6l2.5 5 5.5.8-4 3.9.95 5.5L12 16.2l-4.95 2.6.95-5.5-4-3.9 5.5-.8z"/>',
  play: '<path d="M7 4.8v14.4L19.5 12z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.5-5.8"/><path d="M20 3.5V8h-4.5"/>',
  // Navigation / Medien (Themes X4 + OS 3) ---------------------------------------------
  house: '<path d="M2.5 13 9.5 7l7 6v8h-14z"/><path d="M12.5 9.5 16 6.5l5.5 4.7V21h-5"/>',
  grid: '<rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/>'
    + '<rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/>',
  menu: '<path d="M4 6.5h16M4 12h16M4 17.5h16"/>',
  kebab: '<circle cx="12" cy="5.5" r="1.3" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/>'
    + '<circle cx="12" cy="18.5" r="1.3" fill="currentColor" stroke="none"/>',
  chev_left: '<path d="M15 5.5 8.5 12l6.5 6.5"/>',
  chev_right: '<path d="M9 5.5l6.5 6.5L9 18.5"/>',
  prev: '<path d="M6 5v14"/><path d="M19 5.5v13L9 12z"/>',
  next: '<path d="M18 5v14"/><path d="M5 5.5v13L15 12z"/>',
  volume: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4.2 4.2 0 0 1 0 6M18 6.5a7.8 7.8 0 0 1 0 11"/>',
  vol_mute: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
  logout: '<path d="M14 4.5h4.5v15H14"/><path d="M10 8l-4 4 4 4M6 12h9.5"/>',
  flame: '<path d="M12 3c3.8 4.6 6 7.6 6 11.2A6 6 0 0 1 6 14.2c0-2.4 1.1-4.2 2.6-5.8 0 2.6 1.2 3.9 2.6 3.9-.8-3.2-.1-6.3.8-9.3z"/>',
  snow: '<path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9"/><path d="M9.5 4.5 12 6l2.5-1.5M9.5 19.5 12 18l2.5 1.5"/>',
  calendar: '<rect x="4" y="5.5" width="16" height="15" rx="2"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
  folder_music: '<path d="M3 6.5h6l2 2h10V19H3z"/><path d="M11 17v-6l5-1v5"/><circle cx="9.6" cy="17" r="1.4"/><circle cx="14.6" cy="15" r="1.4"/>',
  user: '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/>',
  palette: '<path d="M12 3.5a8.5 8.5 0 0 0 0 17c1.3 0 1.8-.9 1.8-1.8 0-1.4-1.3-1.6-1.3-2.9 0-1 .8-1.8 1.8-1.8H17a3.5 3.5 0 0 0 3.5-3.5c0-3.9-3.8-7-8.5-7z"/>'
    + '<circle cx="7.8" cy="11.5" r="1.1"/><circle cx="10.5" cy="7.6" r="1.1"/><circle cx="15" cy="7.8" r="1.1"/>',
  // Fernbedienung / Musik ------------------------------------------------------
  rew: '<path d="M11.5 6.5 4 12l7.5 5.5zM20 6.5 12.5 12l7.5 5.5z"/>',
  ffwd: '<path d="M12.5 6.5 20 12l-7.5 5.5zM4 6.5 11.5 12 4 17.5z"/>',
  rec: '<circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/>',
  back: '<path d="M9.5 6.5 4.5 11.5l5 5"/><path d="M4.8 11.5h9.7a5 5 0 0 1 0 10H12"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.6v.2"/>',
  guide: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 9.5h17M9 9.5V19M14.5 9.5V19"/>',
  repeat: '<path d="M4.5 11V9.5a3 3 0 0 1 3-3h12M16.5 3.5l3 3-3 3M19.5 13v1.5a3 3 0 0 1-3 3h-12M7.5 20.5l-3-3 3-3"/>',
  repeat_one: '<path d="M4.5 11V9.5a3 3 0 0 1 3-3h12M16.5 3.5l3 3-3 3M19.5 13v1.5a3 3 0 0 1-3 3h-12M7.5 20.5l-3-3 3-3"/><path d="M11.2 10.6 12.4 10v4.4"/>',
  shuffle: '<path d="M3.5 7h3.6c2.3 0 3.6 1.2 4.9 5s2.6 5 4.9 5h3.6M17.5 4.5 20 7l-2.5 2.5M17.5 14.5 20 17l-2.5 2.5"/><path d="M3.5 17h3.6c1.2 0 2.1-.3 2.8-1M20 7h-3.5c-1.2 0-2.1.3-2.8 1"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  list: '<path d="M8.5 6.5h12M8.5 12h12M8.5 17.5h12"/><circle cx="4.5" cy="6.5" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="17.5" r="1"/>',
  remote: '<rect x="7" y="2.5" width="10" height="19" rx="3"/><circle cx="12" cy="9" r="2.4"/><path d="M10 14.5h.1M14 14.5h.1M10 17.5h.1M14 17.5h.1"/>',
  numpad: '<path d="M6 5h.1M12 5h.1M18 5h.1M6 10h.1M12 10h.1M18 10h.1M6 15h.1M12 15h.1M18 15h.1M12 20h.1"/>',
  note: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.4 3.6 5.2 3.6 8.5s-1.2 6.1-3.6 8.5c-2.4-2.4-3.6-5.2-3.6-8.5S9.6 5.9 12 3.5z"/>',
};
function ic(name, size) {
  const body = ICONS[name] || ICONS.gear;
  const s = size ? ' style="width:' + size + 'px;height:' + size + 'px"' : '';
  return '<svg class="ic"' + (s || '') + ' viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>';
}
