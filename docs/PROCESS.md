# Making the Fernie Topo Map: The Whole Process

This is the human side of the project: everything from an empty laptop to a folded map on a shop counter, and how to make the next edition. The build pipeline handles data and rendering. This document covers the judgement calls, the manual work, and the order to do things in.

The short version: **build, proof on paper, fix, repeat.** The pipeline makes each loop cheap. Most of your time goes into design, labels and checking the map against the real world.

## How the pieces fit

```
open data ──► pipeline (make all) ──► print PDF + proof tiles + QA report
                    ▲                                │
                    │                                ▼
     your fixes: styles, label positions,     print proofs, walk trails,
     corrections layer, OSM edits  ◄──────── ask locals, mark up paper
```

There are four kinds of thing you'll edit, and each fix belongs in exactly one of them:

| What's wrong | Where to fix it |
|---|---|
| A trail or road is missing, wrong or misclassified in the real world's terms | **OpenStreetMap**, with JOSM. Everyone benefits, and it flows into the next build. |
| Something you want shown differently from OSM, or that can't go in OSM (a local name, a closure note, your own trail grading) | **`authored/corrections.gpkg`** |
| It looks wrong: colour, line weight, symbol, draw order | **QGIS styles** (`qgis/styles/`) |
| A label is badly placed | **Label positions in QGIS** (saved to `qgis/fernie.qgd`) |

If you're unsure, ask: "Would a stranger mapping this area want this fix?" If yes, it goes in OSM. If it's specific to this map, it goes in corrections.

## One-time setup

- [ ] Docker and `make` on the build machine.
- [ ] **QGIS desktop**, at the same version the Docker image pins. You'll use it for styling, labels and the corrections layer. A version mismatch can quietly change how labels render, so keep them in step.
- [ ] **JOSM** for OpenStreetMap edits, plus an OSM account.
- [ ] **QField** on your phone, for field checks against the draft map.
- [ ] The fonts the styles use, installed both on your desktop and in the Docker image. A missing font silently falls back to something else and moves every label.
- [ ] Read OSM's guidance on editing and good changeset comments before your first upload. Local mappers notice large, careless edits.

## Before the first real build

- [ ] **Set the map extents** in `config/map.yaml`. Decide what's on side A and side B. A 24 × 36 inch sheet at 1:25,000 covers roughly 14 × 21 km per side after margins.
- [ ] **Check terrain coverage.** Look at the LidarBC portal's coverage map for the Fernie tiles. If there's no lidar, the pipeline falls back to coarser national data, and you'll want to know that before judging the contours.
- [ ] **Choose a contour interval.** Start at 20 m with 100 m index lines. Try 10 m on a proof of the steepest area, such as the Three Sisters or the resort bowls, before deciding.
- [ ] **Write the symbol spec** in `docs/symbol-spec.md`. Base it on maps you already trust at this scale. Swisstopo, Harvey and the old NTS sheets are good references.

## Running the pipeline

```
make all        # everything
make fetch      # just download (cached in .cache/)
make render     # re-render after style or label changes
make proofs     # proof tiles for the LaserJet
make qa         # QA report
```

Outputs land in `build/`. After each build, open these in order:

1. **`build/qa-report.md`**: what changed since the last build, stale corrections, and name conflicts. This is your to-do list.
2. **PNG previews**: a quick sanity check.
3. **Proof tiles**: print and judge on paper.

Deleting `.cache/` is always safe; it only costs download time.

## Designing the symbology

Work from the bottom of the map upward, and get each layer right before adding the next. Otherwise you'll keep re-tuning things that were only wrong because of what sat on top of them.

1. **Terrain.** Hillshade and contours alone. The relief should read clearly before anything goes on it. Hillshade should stay subtle; most first attempts are too dark.
2. **Water and vegetation.** Blue water and pale green forest. Check that the contours still read through the forest tint.
3. **Roads.** Highway, paved, gravel, forest service road and 4×4 track should be distinguishable at arm's length.
4. **Trails.** These are the reason people buy the map, so they get the strongest contrast. Decide your difficulty scheme now and put it in the legend.
5. **Points of interest.** Peaks, huts, trailheads, parking, viewpoints.
6. **Grid and collar.** UTM grid, scale bar, declination diagram, legend, attribution, disclaimer, edition.
7. **Labels last.** Every symbology change moves labels, so don't hand-place labels until the symbols are settled.

Rules of thumb for print:

- Nothing thinner than about 0.1 mm. Hairlines vanish or break up on synthetic stock.
- No text smaller than about 6.5 pt. Check it at arm's length, in dim light, with reading glasses off. That's your customer at a trailhead.
- Test the colours greyscale too. If the map still reads in grey, it will survive bad light and colour-blind readers.
- Screen colours lie. Only a proof on the final paper from the final printer tells you the truth (see Pre-press).

## Labels

Labels take the most time, and they're what makes a map look professional or amateur.

**Automatic pass.** Set up label rules in QGIS before touching individual labels:

- Priorities: town and major peak names highest, then lakes, rivers, trails, minor features, and contour elevations lowest.
- River and creek names follow the line. Lake names sit inside the shape when they fit.
- Contour labels read uphill and sit only on index contours.
- Labels avoid trails and other labels (obstacles). Allow peak labels to move around the peak point.
- Use halos sparingly, in the background colour, and just enough to separate text from contours.

**Manual pass.** Use the QGIS label toolbar to pin, move, rotate or hide individual labels. QGIS stores these edits in auxiliary storage (`qgis/fernie.qgd`), so they survive every rebuild. **Commit that file** after each session.

Work area by area, using the grid squares as the unit, and track which squares are finished. Do the dense areas (town, resort) first while you're fresh.

**Names.**

- Official names come from BC Geographical Names. Where OSM disagrees, the QA report flags it, and you decide.
- Local unofficial names (ski lines, climbing areas, trail nicknames) are a judgement call. Printing a name makes it more official and can send traffic somewhere that people or landowners would rather not have it. Ask the trail association or the people who use the name.
- Decide early whether to include Ktunaxa place names, and if so, talk to the Ktunaxa Nation about which names and spellings. Adding them late means redoing labels.

## Proofing

Print proofs on the LaserJet at **100% scale**: the pipeline makes tiles with crop marks, and you tape them together. Never print fit-to-page; a shrunk proof tells you nothing about legibility.

Mark up in pen. A consistent colour code helps when you transcribe later:

- **Red**: data is wrong (missing trail, wrong road class, bad name)
- **Blue**: design problem (illegible, clashing, too heavy)
- **Green**: label placement

Proof checklist:

- [ ] Every trail legible against the contours and forest tint
- [ ] Smallest text readable at arm's length
- [ ] No labels colliding or sitting on top of trails
- [ ] Legend matches what's actually on the map
- [ ] Grid labels, scale bar and declination present and correct
- [ ] Attribution and disclaimer present and readable
- [ ] Greyscale version still makes sense

The LaserJet is good for legibility and data checks. It's useless for judging final colour or subtle hillshade, which halftoning makes blotchy. Don't tune those on office prints.

## Ground truthing

Do this in three layers, cheapest first.

**At the desk.**

- Work through the change list in the QA report.
- Compare trails and roads with your own GPX tracks and with recent imagery. For OSM edits, only trace from imagery sources OSM permits; JOSM lists them.
- Fix real-world errors in OSM with JOSM, and map-specific ones in the corrections layer.

**In the field.**

- Load the QGIS project in QField and record tracks and notes as you go. Or carry a printed proof and a pen. The paper version also tests whether the map is usable in the wild, which is the point.
- Things to note: trail junctions (the most common source of errors), trailhead and parking locations, gates and closures, signage names compared with map names, and trails that have been decommissioned or rerouted.
- Season matters. Valley trails can be checked most of the year. Alpine trails wait for summer. Ski touring content, if you add any, needs a winter review.

**Expert review.** Give a proof to people who know the ground better than you do: Fernie Trails Alliance, SAR members, guides, the regional district, and the Elk Valley Avalanche and Snow Workshop crowd for anything winter-related. This is the best return for the effort. Ask specific questions ("Is anything on here closed, private or wrong?") rather than "What do you think?"

**Private land.** Showing a trail that crosses private land without permission causes real trouble for landowners and the trail association. Check land status for anything you're unsure of, and when in doubt, leave it off or mark it.

## Pre-press

Once the map is close, start working with a printer. Talk to them early; their constraints change your files.

- [ ] **Choose the paper.** Get samples of two or three synthetic stocks. Test folding, writing with pencil and pen, toughness when wet, and tear resistance. Fold a sample 50 times and look at the creases.
- [ ] **Agree on file specs.** Colour space (CMYK and which ICC profile), bleed, PDF standard, how fonts are embedded, and how they want the two sides supplied. Whether QGIS can produce CMYK output directly is still an open question in the pipeline; the printer may be able to convert for you.
- [ ] **Get a hard proof** on the real stock, from the real press. Judge colour and hillshade only on this. Expect to adjust the hillshade and the forest tint at least once.
- [ ] **Get a folded proof.** Check the fold pattern, that the cover panel ends up on the outside, and that folds don't cut through important areas like the town or the main trailheads.
- [ ] **Cover panel.** Title, area, scale, edition, a small location diagram, price, and a barcode if shops want one.
- [ ] **Run size.** A short digital run first. Selling 100 maps tells you more than printing 1,000.

## Licensing and legal

- **Attribution** is generated from `sources.yaml` and printed in the collar. Check it on every proof. OSM requires credit to OpenStreetMap contributors plus a statement that the data is available under the ODbL. The BC and Canada government data requires the Open Government Licence attribution statements.
- **Don't use Trailforks data.** Their terms forbid it.
- **Disclaimer.** Make clear that the map is a guide, that conditions change, that it doesn't show avalanche hazard unless it explicitly does, and that users are responsible for their own safety. Consider having a lawyer look at the wording, given that people will use this in avalanche terrain. I'm not a lawyer, and this is the one place where paying for an hour of one is clearly worth it.
- **Business basics.** Selling maps is a small business. Check whether you need a business licence locally, and how GST/HST applies to your expected sales. The small-supplier threshold matters here, so confirm the current figure with the CRA.
- **Your own copyright.** The cartography (design, label placement, layout) is your work, even though the data is open. Decide how you want to license the produced map. ODbL allows you to choose.

## Selling

- **Local shops:** outdoor and bike shops, bookstores, the visitor centre, the resort. Ask what margin they expect before setting a retail price.
- **Fernie Trails Alliance:** a partnership (they review the map, and a share of sales supports trails) adds credibility and opens their channels.
- **Digital edition:** the pipeline's GeoPDF can be sold through Avenza Maps, which lets phones show your position on the map. Check Avenza's current publisher terms first.

## Making a new edition

Plan on a yearly edition, or whenever enough has changed. The previous edition's released map is the baseline for the change report, so the first step happens at release time.

**When you release an edition** (do this once per edition):

1. **Archive the release** in `releases/<edition>/`, e.g. `releases/2027.1/`. Copy in:
   - `build/map.gpkg`
   - `build/manifest.json`
   - `build/qa-report.md`
   - `build/print/side-*.pdf`

   `releases/` is git-ignored, because the no-data-in-git rule applies, so keep a backup copy elsewhere too.
2. **Tag the commit** that built it (`git tag 2027.1`) and push the tag. Attach the archived files to a GitHub release for the tag, so the manifest and map travel with it.

**When you start the next edition:**

1. **Set the new edition's values** in `config/map.yaml`:
   - `edition` (e.g. `2028.1`);
   - `osm.snapshot` to the date you want OSM data from (normally today);
   - `collar.declination_date` to the date the edition is meant for;
   - `qa.baseline` to the last release's map (`releases/2027.1/map.gpkg`), so the change report covers everything since that edition, not just since the last build.
2. **Run `make all`.** The OSM fetch is new because the snapshot date changed; terrain stays cached.
3. **Read `build/qa-report.md`:**
   - The **change list** holds the new, removed, reclassified, renamed and reshaped trails, roads, points and places, each with a grid reference and an OSM link. This is the field-checking list.
   - Delete or retarget **stale corrections** (their OSM feature has gone).
   - Settle new **name differences**.
4. **Re-check labels** in areas with changes. Hand-placed labels are keyed to each feature's `source_id` and survive, but new features push things around.
5. **Field-check** what changed, and send proofs (`make proofs`) to your expert reviewers.
6. **Check the collar:** edition, OSM date and declination. Then send the files to the printer.
7. **Release** as above.

Automating the release step (a `make release EDITION=…` that copies the files and tags the commit) is about an afternoon's work. It's worth doing if editions become frequent; for one a year, the steps above are enough.

## Release checklist

- [ ] QA report reviewed, no unresolved items
- [ ] All proof markups addressed
- [ ] Expert reviewers signed off
- [ ] Attribution, disclaimer, edition and declination correct in the collar
- [ ] Hard proof and folded proof approved on the final stock
- [ ] Label positions (`fernie.qgd`) and corrections committed
- [ ] Release archived in `releases/<edition>/` (map.gpkg, manifest, QA report, print PDFs), tagged, and attached to a GitHub release
- [ ] Shops lined up before the boxes arrive
