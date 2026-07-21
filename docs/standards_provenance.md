# Standards provenance — hyperscale_fire module pins

Convention mirrored from Claude-Spec-Critic's `docs/standards_provenance.md`:
every pinned edition in `backend/spec_modules/hyperscale_fire.py` carries a
machine-readable `source`; this file holds the receipts. Entries whose
`source` begins with `UNVERIFIED` have not been confirmed against a
published listing — none currently do, but future additions must follow the
same rule.

**Edition posture.** These pins are *drafting defaults*: the current
published edition of each standard as of the verification date. This is
deliberately different from Spec Critic's `datacenter_fire` review module,
which pins the editions the 2024 I-codes *reference* (e.g. NFPA 13-2022) —
a review runs against a stated code basis, while drafting defaults to the
current edition per the frozen project decision (CLAUDE.md). The
jurisdiction's adopted edition, once stated by the user (or grounded by
Phase 4 research), overrides a pin through the `set_standard_edition`
operation with its adoption basis recorded — never silently.

**Verification pass: 2026-07-21** (web, this repo's Phase 3 build session).
Editions confirmed via publisher/retailer listings (nfpa.org product pages,
ANSI webstore, ICC shop, NFSA TechNotes, UpCodes). Note that NFPA product
listings are public records of what is published; paywalled standard text
was not needed to confirm edition years.

## Consolidation check (important negative finding)

A consolidation of NFPA 13D / 13R / 24 / 291 into NFPA 13-2025 was
**checked for and did not happen** — each has its own current edition
(13D-2025 and 13R-2025 exist as separate documents; NFPA 24-2025 and
NFPA 291-2025 are published separately). NFPA 24 therefore remains its own
pin. Sources: separate 2025-edition listings for NFPA 13 / 13D / 13R
(nfpa.org, nfpanorm previews, NFSA TechNotes 2024-07 "Changes in the 2025
Edition of NFPA 13" — which describes content changes only, no absorption
of sibling standards), NFPA 24-2025 (NFPA catalog, ANSI webstore, Amazon),
NFPA 291-2025 (ANSI webstore, Amazon).

## Pin receipts (current editions as of 2026-07)

| Standard | Pinned | Confirmed via |
|---|---|---|
| NFPA 13 | 2025 | nfpa.org product listing; UpCodes NFPA 13-2025; NFSA TechNotes 2024-07 (issued fall 2024) |
| NFPA 14 | 2024 | NFSA TechNotes 2024-10 "Updates to NFPA 14"; publisher previews |
| NFPA 20 | 2025 | NFPA 20-2025 publisher/preview listings |
| NFPA 22 | 2023 | nfpa.org blog 2024-10 "NFPA 22 and Water Storage Tanks"; link.nfpa.org/all-publications/22/2023; no newer edition found 2026-07 |
| NFPA 24 | 2025 | NFPA catalog; ANSI webstore NFPA 24-2025 |
| NFPA 25 | 2026 | QRFS "NFPA 25 2026 Edition: Key Updates"; Amazon/AtHomePrep 2026-edition listings (issued fall 2025) |
| NFPA 72 | 2025 | nfpa.org product page NFPA 72-2025 |
| NFPA 75 | 2024 | ANSI webstore NFPA 75-2024; UpCodes | 
| NFPA 76 | 2024 | ANSI webstore NFPA 76-2024; UpCodes |
| NFPA 291 | 2025 | ANSI webstore NFPA 291-2025 |
| NFPA 2001 | 2025 | ANSI webstore NFPA 2001-2025; publisher listings |
| NFPA 855 | 2026 | Telgian "NFPA 855 Changes in the 2026 Edition"; ICC shop; Energy-Storage.News |
| IBC | 2024 | ICC — current published I-code edition (2027 cycle in development) |
| IFC | 2024 | ICC — current published I-code edition |

## Maintenance

- NFPA revision cycles run ~3 years; expect NFPA 14 (2027?), NFPA 22, and
  the 2028-cycle standards to move. Re-verify when a phase touches the pin
  list, and whenever a drafted spec will be issued (the app's lint checks
  the draft against these pins, so a stale pin propagates).
- When updating a pin: bump the edition, update `source` with the new
  confirmation, and add a row here. If an edition cannot be confirmed,
  prefix the `source` with `UNVERIFIED` — registry validation requires a
  non-empty source either way, and `StandardsBasis.unverified_standards()`
  exposes the unconfirmed set.
- Owner-invoked standards (NFPA 75/76) are pinned at current editions;
  they are commonly invoked by hyperscaler programs rather than mandated
  by code — keep them pinned so REFERENCES articles cite real editions.
