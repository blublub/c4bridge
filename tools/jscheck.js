// Syntax-Check aller <script>-Bloecke einer HTML-Datei: node jscheck.js <datei.html>
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
let bad = 0;
blocks.forEach((m, i) => {
  try { new Function(m[1]); console.log(`script ${i}: OK (${m[1].length} Zeichen)`); }
  catch (e) { bad++; console.log(`script ${i}: FEHLER: ${e.message}`); }
});
if (!blocks.length) console.log('keine <script>-Bloecke gefunden');
process.exit(bad ? 1 : 0);
