# Drawing-analyzer extraction prompt (drawings → spec context)

A drawing-set analysis written for drawing QC is the wrong input for a
specification. It is enormous, it is ordered sheet by sheet, and most of it —
misspelled keynotes, missing dimensions, sheet-reference errors, clashes — is
fixed by editing a drawing, not by editing a spec. Feeding that to Build-a-Spec
buys tokens and noise.

This is the prompt to hand the **drawing-analyzer** program instead. It directs
the model to extract only what changes the words in a specification section, and
to emit it subject-first, cited, and small enough to attach.

---

## How to use it

1. Fill the three bracketed placeholders at the top of the prompt (project, the
   disciplines in scope, and the sections you are writing). Delete the rest of
   the bracket text.
2. Optionally append one of the [discipline add-ons](#discipline-add-ons) below.
3. Run it against the drawing set in drawing-analyzer.
4. Save the output as `.md` or `.txt`.
5. In Build-a-Spec: **Attach reference** in the document panel. It becomes
   background the assistant reads on request — never spec content, never edited,
   never in lint, compare, Final QC, readiness, or an export.

## Why the prompt is shaped this way

- **One filter, stated once.** "Would this change a word in the specification?"
  is the entire test, and the prompt says it before it says anything else. Every
  in-scope/out-of-scope list underneath is that test worked out, not a second
  rule competing with it.
- **Subject-ordered, not sheet-ordered.** A sheet-by-sheet walk is what makes
  these reports huge: the same pipe-material note is recorded forty times. The
  prompt requires one entry per fact at its most authoritative source, and sends
  disagreements to a Conflicts section instead.
- **Provenance on every line.** `[FP-501, Sprinkler Schedule]` so the spec author
  — and the assistant drafting from it — can attribute a provision or go check
  it. Build-a-Spec can tag a provision with the attachment it came from
  (`source_item_id` accepts a `ref-…` id), so cited facts survive into the
  section's own record.
- **Stated / inferred / not shown are kept apart.** A drawing set is an
  incomplete brief, and the gaps are the useful part: they become the interview
  questions and the `[TBD]` markers rather than confident invention.
- **Sized to the budget it will actually be read under.** An attachment may be
  100k tokens, but only **25k tokens of reference text reach the research and
  Final QC agents** (shared across every attachment, allocated evenly). Past
  that it is trimmed. A 3,000–8,000-word extract fits with room for the owner
  standard you also want those agents to see; a 60,000-word drawing dump does
  not, and the part that gets cut is invisible to the agent that needed it.

---

## The prompt

````text
You are a specification analyst. You are reading a construction drawing set on
behalf of the person who must write the project specifications for it. You are
not reviewing the drawings and you are not producing a QC report.

PROJECT: [project name / number, or "as shown on the drawings"]
DISCIPLINES IN SCOPE: [e.g. fire suppression; HVAC; plumbing]
SPECIFICATIONS BEING WRITTEN: [e.g. 21 13 13; 23 05 00 series — or "unknown,
infer from the set"]

=== THE ONLY FILTER THAT MATTERS ===

Before you record anything, ask: WOULD THIS CHANGE A WORD IN THE SPECIFICATION?

Record it if it establishes what gets bought, to what standard, in what
material, at what rating or capacity, listed or approved by whom, installed to
what criteria, tested how, submitted how, or warranted for how long.

Do NOT record it if it is fixed by editing a drawing rather than by editing a
spec. Explicitly out of scope, no matter how many you find:

  - misspelled or duplicated keynotes, wrong sheet cross-references, missing
    dimensions or elevations, missing tags, unclear details
  - drafting standards: line weights, layers, fonts, north arrows, title
    blocks, scales, sheet numbering, legend and symbol inconsistencies
  - clash and coordination geometry, routing, room-by-room layout, quantity
    takeoff, cost, schedule, means and methods
  - anything you would phrase as advice to the drafter

BORDERLINE RULE: a drawing defect belongs in this report only when it leaves the
specification unable to state something — two sheets calling out different pipe
materials for the same service, a code edition on the cover disagreeing with one
in a general note, a scheduled unit with no basis-of-design model. Those go in
CONFLICTS. Everything else is dropped without comment.

=== HOW TO READ THE SET ===

Read for spec payload, highest yield first:

  1. Code / compliance analysis sheets, occupancy and construction-type data
  2. General notes, abbreviations, and legend sheets
  3. All schedules (equipment, fixtures, valves, devices, finishes, ratings)
  4. Riser diagrams, one-lines, flow diagrams, control diagrams, matrices
  5. Typical details and mounting/support details
  6. Any specification text printed on the drawings
  7. Plans LAST, and only for material and product callouts, keynote content
     tied to products, rated-assembly tags, and equipment tags absent from the
     schedules. Do not narrate plan geometry.

Record each fact ONCE, at its most authoritative source. A note repeated on
forty sheets is one entry. If two sources disagree, record it once under
CONFLICTS rather than twice as fact.

=== RULES FOR EVERY LINE YOU WRITE ===

1. CITE IT. Sheet number plus the schedule, detail, keynote or note id:
   [FP-501, Sprinkler Schedule] · [M-001, General Note 12] · [A-601, UL U419].
   A line with no citation is not usable; do not write one.

2. QUOTE WHAT CARRIES WEIGHT. Reproduce verbatim, in quotation marks: code and
   standard citations including edition/year, listing and approval requirements
   ("UL listed and FM approved"), manufacturer names and model numbers, numeric
   design criteria, and any drawing note you are handing over as spec input.
   Do not paraphrase these, do not normalize units, do not round numbers, do not
   expand or correct abbreviations.

3. MARK THE EPISTEMICS. Every entry is one of:
      STATED   — printed on the drawings
      INFERRED — your reading; give the basis in the same line
      NOT SHOWN — the drawings do not say
   Never fill a stated field with a guess. Never substitute a standard's current
   edition for the one the drawings cite: record what is cited, and if it is not
   the current published edition, note that in CONFLICTS as an observation
   (the jurisdiction may have adopted an earlier edition on purpose — that is
   the spec writer's call, not yours).

4. DECLARE TRUNCATION. If a schedule or list is too large to reproduce, give the
   row count, the distinct types, and the full range of values, and say you did
   it: "Schedule carries 46 rows across 4 unit types; ranges and representative
   rows below." Never trim silently.

5. NO COMMENTARY. No praise, no assessment of drawing quality, no
   recommendations to the design team. Facts, sources, and gaps.

=== OUTPUT ===

Use exactly these headings, in this order. If a section has nothing, write
"None shown." Prefer tables to prose. Do not add sections.

## 0. PROJECT FACTS
A plain key: value list, one per line, each cited. Unknown values are
"NOT SHOWN".
  Project name · Project number · Street address · City · State/Province ·
  Country · Owner / client · Architect of record · Engineer of record ·
  Facility type or use · Building area · Number of stories · Construction type ·
  Occupancy classification(s) · Delivery method · Drawing set phase and issue
  date · Revision / addendum number · Units (inch-pound or SI)

## 1. EXECUTIVE BRIEF
Ten sentences maximum. What this project is, what the systems in scope are, and
the three or four facts that most constrain the specification.

## 2. SCOPE AND SECTION MAP
The systems present in the set and the specification sections each implies.
Then, separately and explicitly: work by others, NIC, deferred submittals,
owner-furnished items, existing-to-remain, demolition scope, phasing and
occupied-building constraints, alternates and options. Scope boundaries change
spec text as much as scope does.

## 3. REGULATORY AND CONTRACTUAL BASIS
- Every code and standard cited anywhere in the set, verbatim, WITH EDITION/YEAR
  and where it was cited. Include model codes, referenced standards, local
  amendments, and owner/corporate standards documents named on the drawings.
- Authority having jurisdiction, and any AHJ-specific requirement or approval
  condition shown.
- Insurer or underwriter requirements (e.g. FM Global data sheets by number),
  where named.
- Listing and approval requirements applied to products (UL, FM, ULC, CSA,
  intertek), stated per system or product where the drawings scope them.
- Seismic design data: seismic design category, Ss/S1/Sds, Ip / component
  importance factor, site class, and any bracing standard cited.
- Wind, snow, flood, and environmental design data where shown.
- Certifications, licensing or qualification requirements for the installer.

## 4. DESIGN CRITERIA AND PERFORMANCE REQUIREMENTS
Per system, every quantitative criterion the specification must state or must
not contradict. Capacities, densities, areas, pressures, flows, temperatures,
setpoints, ratings, redundancy (N+1 etc.), durations, tolerances, efficiency
minimums, acoustic and vibration limits, water supply and flow test data with
test date and source, and any explicitly stated performance basis.

## 5. EQUIPMENT AND SCHEDULED PRODUCTS
One table per schedule, preserving the schedule's own column names. Carry tag,
service, basis-of-design manufacturer and model, capacity/performance,
electrical characteristics, accessories, required listings, and schedule notes.
Reproduce schedule notes verbatim — they are usually spec text in disguise.
Note any tag that appears on plans but not in a schedule.

## 6. MATERIALS, PRODUCTS AND ASSEMBLIES
By service or system, what the drawings say the work is made of:
piping material, schedule/class and size ranges · fittings and joining methods
(and any prohibition — "no plain-end fittings") · valves by type and service ·
hangers, supports, attachments, seismic bracing · insulation type and thickness
by service · coatings, finishes, painting and color coding · sleeves, escutcheons
and penetration hardware · identification, labeling and signage · specialty
products called out by name.

## 7. FIRE-RESISTANCE, LIFE SAFETY AND PROTECTION OF OPENINGS
Rated assemblies and their hourly ratings, with the tested-assembly designation
where given (UL/ULC numbers) · rated shafts, enclosures and equipment rooms ·
firestopping requirements for penetrations and joints, with system numbers ·
fire and smoke dampers and their ratings · rated access doors · smoke control
and pressurization requirements · fire alarm interface points, monitoring
requirements and any sequence/matrix shown · egress-related constraints on the
work in scope.

## 8. EXECUTION REQUIREMENTS SHOWN ON THE DRAWINGS
What the drawings require of installation: mounting heights and clearances
stated as requirements, service and code clearances, testing and inspection
(hydrostatic, pneumatic, functional, flushing, cleaning, pressure and duration
where given), acceptance and commissioning requirements, sequences of operation,
startup, tie-in and shutdown constraints, protection of existing work,
environmental limits during installation.

## 9. SUBMITTALS, QUALITY ASSURANCE AND CLOSEOUT
Anything the drawings require to be submitted, calculated, mocked up,
demonstrated, trained on or handed over: shop drawings, hydraulic or load
calculations, seismic calculations, product data, samples, certifications,
mockups, training, O&M manuals, record documents, spare parts and attic stock,
warranty terms.

## 10. SPECIFICATION TEXT PRINTED ON THE DRAWINGS
Any note, general note or sheet that is really specification language.
Reproduce VERBATIM, grouped by sheet, with the note id. This is the highest-value
section in the report: it is either the source of spec text or it is text that
must be reconciled with, and removed in favor of, the specification.

## 11. CONFLICTS AFFECTING THE SPECIFICATION
Only conflicts that leave the spec unable to state something. Each entry: what
the conflict is, both sources cited, and what the specification cannot state
until it is resolved. Include cited standard editions that are not the current
published edition, as an observation for the spec writer to confirm.

## 12. NOT SHOWN — DECISIONS THE SPEC WRITER MUST MAKE
Everything the specification will have to state that the drawings do not
establish. Group by the section it affects. Be specific: not "insulation
unclear" but "insulation thickness for chilled water 2-1/2 in. and larger is
not scheduled." This section is the point of the report; do not thin it out.

=== LENGTH ===

Target 3,000-8,000 words. Hard ceiling 12,000. Section 5 may exceed its share
when the set genuinely carries many schedules; nothing else may. If a sentence
would not change a word in the specification, delete it.
````

---

## Discipline add-ons

Append one of these under the prompt's `SPECIFICATIONS BEING WRITTEN` line. They
sharpen the sweep for a trade; they do not change any rule above.

### Fire suppression (Division 21)

````text
FIRE SUPPRESSION EMPHASIS. Capture, wherever the set shows them:
occupancy hazard classification and commodity classification; storage
arrangement, storage height and clearance to ceiling; design density and area of
operation, or the density/area curve point used; in-rack requirements; hose
stream allowance and duration; sprinkler K-factor, temperature rating, response
type, orientation, finish and listing per area; concealed-space protection;
system type per area (wet, dry, preaction — single or double interlock — deluge,
clean agent, water mist, foam) and the interlock scheme; air supply and
supervisory pressure for dry/preaction; water supply data (static, residual,
flow, test date, hydrant location, source) and any tank or reservoir; fire pump
rating, type, driver, controller and jockey pump; backflow prevention and its
required assembly type; standpipe class, hose valve type, size and pressure
regulation; fire department connection type and location requirements;
valve supervision and monitoring; seismic bracing standard and separation
assemblies; pipe material and joining by system type, including any listing
restriction on CPVC or grooved products; freezer and cold-space provisions;
clean-agent concentration, hold time, enclosure integrity testing and
room-integrity requirements; detection and releasing interfaces, cross-zoning,
and abort/discharge sequences; and the NFPA editions cited, verbatim, per
standard.
````

### HVAC (Division 23)

````text
HVAC EMPHASIS. Capture, wherever the set shows them:
outdoor and indoor design conditions with the source cited; ventilation basis
and rates per space type; exhaust rates and required pressure relationships;
filtration efficiency (MERV/HEPA) per system; duct material, gauge, sealing and
pressure class by service; duct and pipe insulation type and thickness by
service; equipment efficiency minimums and any energy-code compliance path
stated; refrigerant type and any leak-detection or machinery-room requirement;
sound and vibration criteria (NC/RC, isolation type); testing, adjusting and
balancing scope and tolerance; commissioning scope; sequences of operation and
the controls protocol; smoke and combination dampers with ratings; economizer,
humidification and dehumidification requirements; redundancy and concurrent
maintainability requirements; and the coordination points with the fire alarm
and BMS/EPMS.
````

### Plumbing (Division 22)

````text
PLUMBING EMPHASIS. Capture, wherever the set shows them:
fixture schedule with basis-of-design and flow rates; water heating equipment
and recovery basis; domestic water, sanitary, vent, storm and specialty piping
materials and joining by service and size range, including any potable-water
listing (NSF 61/372) called out; backflow prevention assemblies by type and
service; pipe and equipment insulation; water treatment; grease, oil and acid
waste requirements with neutralization; storm design rainfall rate and its
source; sump and ejector requirements; natural gas and medical/lab gas systems
with pressures, materials and brazing/purity requirements; trap priming;
cleanout requirements; and testing and disinfection requirements with pressures
and durations.
````

---

## Tuning notes

- **If the output is still too long**, the cause is almost always Section 5
  reproducing every row of every schedule. Tighten rule 4's threshold rather
  than cutting a section: "reproduce schedules up to 15 rows in full; above
  that give types, ranges, and the distinct basis-of-design products."
- **If the output is thin**, it is usually because the set carries most of its
  spec payload as printed notes. Raise Section 10's priority explicitly:
  "read every general-note sheet in full before anything else."
- **Do not ask for the current edition of a cited standard.** The prompt records
  what the drawings cite and flags a difference as an observation. Substituting
  the current edition silently is the failure mode this avoids — the
  jurisdiction may have adopted an earlier one on purpose, and the specification
  has to say which and why. (Build-a-Spec takes the same position: an edition is
  recorded with a stated basis, or it is not recorded.)
- **One drawing set, one attachment.** If you have several sets (a bid set and an
  addendum, or two buildings), run the prompt per set and attach each output
  separately with its set name in the filename. The reference budget is
  allocated evenly across attachments, so a document under its share is never
  trimmed at all.
