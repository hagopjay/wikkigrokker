// Pairwise pass/fail of a categorical palette, using the dataviz validator's own checks.
// usage: node pair_matrix.mjs VALIDATOR_PATH "#hex,#hex,..." SURFACE
import { pathToFileURL } from "node:url";
const [, , vpath, list, surface] = process.argv;
const { validate } = await import(pathToFileURL(vpath).href);
const pal = list.split(",");
const out = [];
for (let i = 0; i < pal.length; i++) for (let j = i + 1; j < pal.length; j++) {
  const r = validate([pal[i], pal[j]], { mode: "dark", surface, pairs: "all" });
  const checks = Object.fromEntries((r.checks || r.results || []).map(c => [c.name || c.check, c.pass ?? c.status]));
  out.push({ i, j, a: pal[i], b: pal[j], pass: r.pass ?? r.ok, checks });
}
console.log(JSON.stringify(out));
