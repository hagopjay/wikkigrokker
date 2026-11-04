# Loom ledger: WikiData.x3d

How the Loom techniques were used in this project, and how each was changed to fit it.
Started 2026-11-04 at the request "record and log and loom techniques used and modified".

Marks follow the Loom convention: **C** = ran or recomputed here, **L** = stated in a document or note,
**S** = judgment. Evidence lives in `events.jsonl` (hash-chained; `python3 loom/loomlog.py verify`)
and in `receipts/`.

## Witnesses run on the Step 5 viewer

| Witness | What it checks here | How it was changed for this project | Result | Mark |
| --- | --- | --- | --- | --- |
| Grammar | Every X3D element is a real X3D 4.0 node; every attribute is a field of that node; values pass type and range checks; DEF before USE | The X3D schema (XSD) host is blocked here, so two other routes are used: route 1 is the Web3D object model `x3d.py`, route 2 is X_ITE's own parser (`x3d-tidy`). X_ITE extension nodes (`InstancedShape`, `BlendMode`, `DepthMode`) are allowed by name in the live scene only; the static scene must be pure X3D 4.0 | Route 2 caught two containment faults route 1 passed (a `Sphere` and a `FontStyle` directly in a `Group`); both fixed; 0 errors on both routes since | C |
| Eyes | The 3D view is not blank: share of lit pixels and number of hue bins in the canvas, in four views | Runs headless Chromium with software WebGL; a blank page is the negative control and must fail | Caught three blank or broken views that every other check passed (see lessons) | C |
| Host | The page loads with no page errors | Run under a local replica of the artifact host's content policy, with X_ITE served from a second origin that stands in for the CDN | Passes except for blocked font, stylesheet and tangent-helper requests by X_ITE, none fatal | C |
| Behaviour | Clicking a class moves it to the screen centre, the pixel there has the class colour, its label sits beside it, the panel follows | Uses the page's own projection, so labels and picks are checked against the rendered pixels | Pass for `human` (hue within 13 degrees) | C |
| Truth | Numbers agree from source to screen | Three gates: clickstream rows and click sums reconcile (99.66% of clicks resolve to articles); subtree counts equal `primary_count` for all 7,142 visible classes; the root count on the page (7,012,970) equals the data | Pass | C |
| Receipts | What was built from what | A hash-chained event log plus one receipt per build: input and output sha256, each verdict, and what a pass does not establish | `receipts/step5_viewer_2026-11-04.json` | C |
| Palette (new) | Neighbouring branch colours stay distinguishable | The dataviz validator's adjacent pairlist became a spatial one: two branches are neighbours when their classes come within 20 degrees of each other in the ball; colours are assigned so no neighbouring pair fails | 0 failing pairs | C |

Not used: Budget (no spend to cap in this run), Ear and Score (no audio), Meaning and Traps as
separate witnesses (their checks are folded into Truth and the Grammar self-test).

## Patterns from "The Loom: deep review"

| Pattern | Used how | Changed how | Mark |
| --- | --- | --- | --- |
| A1 Test outcome, not structure | Row and sum reconciliation in the clickstream load and the export; the page's root count checked against the data | Extended from data to pixels: Behaviour checks the colour under a class's projected position | C |
| A3 Print what a pass does not establish | Every report and the receipt carry a `not_established` list | None | C |
| A4 Two-sided fixtures | Grammar self-test plants 7 faults and must catch 6 or more; Eyes must fail a blank page | Negative control added to a visual witness | C |
| A5 Don't let `assert` guard a gate | Gates are explicit check dictionaries; outputs are written to `.tmp` and renamed only when every check passes | None | C |
| A6 Bind results to content hash | Receipt hashes; the CDN copy of X_ITE is pinned by a sha384 integrity hash of the npm file that was tested | Applied to a third-party runtime, not only to outputs | C |
| B7 "Unknown" is first-class | The Unclassified branch is drawn; untyped articles are counted in the footer; empty lists say "none listed" | None | C |
| C5 Code owns verdicts | Witness scripts emit pass or fail; judgments in this ledger are marked S | None | C |
| N3 Fixed hand-off shape | The closing summary gives the request, the state, where things are, the numbers, the witnesses, the lessons and open items | No spend line (none incurred) | S |
| N5 Taste as instrument of record | Screenshots were reviewed after each change; the first topic shell passed every check but was redesigned after looking at it | Eyes screenshots are kept in `viewer/witness/` | C |
| N8 Verify fixes as hard as faults | Every fix re-ran the full witness set | None | C |
| N10 Prove one thing end to end | One class (`human`) followed from data to pixel to label to panel | None | C |

## Lessons (each became a rule)

1. **Read success from the stage that did the work.** A Grammar run reported exit 0 while the tool had crashed, because the shell printed the status of `tail`. This is the same failure as the truncated `categorylinks` load. Rule: witness pipelines use `pipefail` and check that their output exists. (C)
2. **Valid is not visible, three times.** (a) A typed-array bug gave a valid scene that drew nothing. (b) The first topic shell passed every check and still covered the ball. (c) Under the host's content policy, X_ITE kept its view hidden because its stylesheet was blocked. Only Eyes or a human look caught each one. Rule: no visual change ships without an Eyes run and a screenshot review. (C)
3. **Two grammar routes catch different faults.** The object model checks names, types and ranges; the parser checks containment. Rule: run both. (C)
4. **Test the host, not just the page.** Rule: Host runs under a replica of the target's content policy, with third-party code on a separate origin. (C)
5. **What gets published must be readable first.** A 1.3 MB minified library cannot be read in full, so it is loaded from a pinned, integrity-checked CDN copy instead of being bundled. (C)
6. **Animated scenes need a pause hook.** Software rendering never settles while a scene animates. Rule: the viewer exposes `wg.state.frozen` for capture, and motion is tested separately. Motion is not yet tested. (C)

## Proposed additions to the shared rulesets

| Rule | Witness | Failing fixture | Passing fixture |
| --- | --- | --- | --- |
| Pipe status from the producing stage | Truth, Grammar | `tool | tail` with the tool crashing | Same with `pipefail` and an output-exists check |
| Policy-twin host | Host | X_ITE from a second origin with `style-src` inline-only: blank view | Same with inline layout rules in the shadow root |
| Spatial pairlist for categorical colour in 3D | Palette | Default slot order: 5 of 9 neighbouring pairs fail | Assigned order: 0 of 9 fail |
| Two-route grammar | Grammar | `Sphere` directly in `Group`: route 1 passes, route 2 warns | Geometry defined inside its shape |
| Negative control for Eyes | Eyes | Blank page must fail | Rendered page passes |
