/**
 * Every Tailwind colour utility names a colour that exists.
 *
 * Tailwind v4 generates no CSS for a class whose colour is neither a
 * `--color-*` token in `src/index.css`'s `@theme` nor one of its own default
 * colours — silently. An undefined border colour then falls back to
 * `currentColor` and draws a bright line; an undefined text or background
 * colour does nothing at all. `border-line bg-paper-2` (the Documents strip,
 * then Final QC's batch line) and `text-danger` (an update failure in Help,
 * the Documents strip's remove button) all shipped that way.
 *
 * The scan reads every string literal in `src/**\/*.tsx` — class lists also
 * live in constants and lookup tables, not only in `className=` — takes each
 * whitespace-separated token that has the shape of a colour utility, and
 * requires its colour to be a theme token, a default-palette colour at a
 * real shade, or a built-in keyword. The default palette is read from the
 * installed `tailwindcss/theme.css`, so it is the palette the build actually
 * has, whatever version `npm ci` installed.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const srcDir = join(root, "src");

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...tsxFiles(path));
    else if (entry.name.endsWith(".tsx")) out.push(path);
  }
  return out.sort();
}

/** The app's own tokens: every `--color-*` declared in index.css. */
function themeTokens(): Set<string> {
  const css = readFileSync(join(srcDir, "index.css"), "utf8");
  return new Set([...css.matchAll(/--color-([a-z0-9-]+)\s*:/g)].map((m) => m[1]));
}

/**
 * Tailwind's default colours, from the installed theme: `red` for
 * `--color-red-500`, and each full name (`red-500`) so a shade must be one
 * the palette defines.
 */
function defaultColours(): { families: Set<string>; names: Set<string> } {
  const css = readFileSync(join(root, "node_modules", "tailwindcss", "theme.css"), "utf8");
  const names = new Set([...css.matchAll(/--color-([a-z0-9-]+)\s*:/g)].map((m) => m[1]));
  const families = new Set([...names].map((n) => n.replace(/-\d+$/, "")));
  return { families, names };
}

const BUILT_IN = new Set(["white", "black", "transparent", "current", "inherit"]);

/**
 * Utility prefixes whose value can be a colour, longest first so
 * `border-t-` wins over `border-`. Each maps to the non-colour values it
 * also takes; a value in that set, a number, a percentage or an arbitrary
 * `[…]`/`(…)` value is not a colour and is skipped.
 */
const NUMERIC = /^(\d+(\.\d+)?%?|px)$/;
const SIDES = ["x", "y", "t", "r", "b", "l", "s", "e"];
const PREFIXES: [string, Set<string>][] = [
  ...SIDES.map((s): [string, Set<string>] => [`border-${s}`, new Set()]),
  [
    "border",
    new Set([
      ...SIDES,
      "solid",
      "dashed",
      "dotted",
      "double",
      "hidden",
      "none",
      "collapse",
      "separate",
    ]),
  ],
  ["ring-offset", new Set()],
  ["ring", new Set(["inset"])],
  ["outline", new Set(["none", "hidden", "solid", "dashed", "dotted", "double"])],
  ["divide", new Set(["x", "y", "x-reverse", "y-reverse", "solid", "dashed", "dotted", "double", "none"])],
  ["decoration", new Set(["solid", "double", "dotted", "dashed", "wavy", "auto", "from-font", "clone", "slice"])],
  ["placeholder", new Set()],
  ["caret", new Set()],
  ["fill", new Set(["none"])],
  ["stroke", new Set(["none"])],
  ["from", new Set()],
  ["via", new Set()],
  ["to", new Set()],
  [
    "text",
    new Set([
      "xs",
      "sm",
      "base",
      "lg",
      "xl",
      ...Array.from({ length: 8 }, (_, i) => `${i + 2}xl`),
      "left",
      "center",
      "right",
      "justify",
      "start",
      "end",
      "wrap",
      "nowrap",
      "balance",
      "pretty",
      "ellipsis",
      "clip",
    ]),
  ],
  [
    "bg",
    new Set([
      "fixed",
      "local",
      "scroll",
      "auto",
      "cover",
      "contain",
      "center",
      "top",
      "bottom",
      "left",
      "right",
      "repeat",
      "no-repeat",
      "none",
    ]),
  ],
];
/** bg- values that are families of non-colour utilities (`bg-clip-text`). */
const BG_NON_COLOUR = /^(clip|origin|repeat|blend|linear|radial|conic|gradient|size|position|top|bottom|left|right)-/;

type Token = { file: string; line: number; token: string; colour: string };

/**
 * The colour a class token names, or null when the token is not a colour
 * utility. Variants (`hover:`, `md:`), `!important` and a negative sign are
 * stripped; an opacity suffix (`/15`) is dropped from the colour.
 */
function colourOf(raw: string): string | null {
  const utility = raw.replace(/^(?:[a-z0-9-]+:)+/, "").replace(/^[!-]/, "").replace(/!$/, "");
  for (const [prefix, nonColour] of PREFIXES) {
    if (!utility.startsWith(`${prefix}-`)) continue;
    const value = utility.slice(prefix.length + 1).replace(/\/\d+$/, "");
    if (!value || nonColour.has(value) || NUMERIC.test(value)) return null;
    if (value.startsWith("[") || value.startsWith("(")) return null;
    if (prefix === "bg" && BG_NON_COLOUR.test(value)) return null;
    if (prefix === "outline" && value.startsWith("offset")) return null;
    if (prefix === "decoration" && /^\d/.test(value)) return null;
    return value;
  }
  return null;
}

/**
 * A string literal is read as a class list only when every token has a
 * class's shape — so prose ("a to-do list", "the bg-heavy page") is never
 * scanned — and only its colour-utility tokens are checked. `${…}`
 * interpolations in a template literal are cut out first.
 */
const CLASS_SHAPE = /^[!-]?(?:[a-z0-9-]+:)*[!-]?[a-z0-9][a-z0-9-]*(?:\/\d+)?(?:\[[^\s\]]*\])?!?$/;
/**
 * Words that have a colour utility's shape but are prose, found by this scan
 * (a one-word string cannot be told from a one-class list by its shape).
 * Keep it short: each entry is a word the scan would otherwise misread.
 */
const PROSE = new Set(["to-do"]);
const STRING_LITERAL = /"([^"\\\n]*)"|'([^'\\\n]*)'|`([^`\\]*)`/g;

/**
 * Every string literal in `text`, as its body (interpolations cut out) and
 * where it starts. A template literal's `${…}` is scanned too, because a
 * conditional class list is usually written as quoted strings inside one
 * (`${failed ? "border-warn" : "border-edge"}`).
 */
function* stringLiterals(text: string, offset: number): Generator<{ body: string; index: number }> {
  for (const m of text.matchAll(STRING_LITERAL)) {
    const index = offset + (m.index ?? 0);
    if (m[3] === undefined) {
      yield { body: m[1] ?? m[2] ?? "", index };
      continue;
    }
    const start = (m.index ?? 0) + 1;
    for (const part of m[3].matchAll(/\$\{([^}]*)\}/g)) {
      yield* stringLiterals(part[1], offset + start + (part.index ?? 0) + 2);
    }
    yield { body: m[3].replace(/\$\{[^}]*\}/g, " "), index };
  }
}

function scan(): Token[] {
  const found: Token[] = [];
  for (const file of tsxFiles(srcDir)) {
    const text = readFileSync(file, "utf8");
    for (const { body, index } of stringLiterals(text, 0)) {
      const tokens = body.split(/\s+/).filter(Boolean);
      if (tokens.length === 0 || !tokens.every((t) => CLASS_SHAPE.test(t))) continue;
      const line = text.slice(0, index).split("\n").length;
      for (const token of tokens) {
        const colour = PROSE.has(token) ? null : colourOf(token);
        if (colour) found.push({ file: relative(root, file), line, token, colour });
      }
    }
  }
  return found;
}

function known(colour: string, theme: Set<string>, palette: ReturnType<typeof defaultColours>) {
  if (theme.has(colour) || BUILT_IN.has(colour)) return true;
  // A default colour must name a shade the palette defines (`amber-500`);
  // a bare family (`amber`) generates nothing.
  return palette.names.has(colour);
}

test("the theme and the default palette are read", () => {
  const theme = themeTokens();
  for (const token of ["edge", "err", "ink-dim", "paper", "accent"]) assert.ok(theme.has(token), token);
  const palette = defaultColours();
  assert.ok(palette.names.has("amber-500"));
  assert.ok(palette.families.has("red"));
});

test("the scanner recognises colour utilities and leaves everything else alone", () => {
  for (const [token, colour] of [
    ["text-err", "err"],
    ["hover:text-err", "err"],
    ["bg-accent/15", "accent"],
    ["border-t-edge", "edge"],
    ["md:hover:border-x-danger", "danger"],
    ["ring-offset-bg", "bg"],
    ["!bg-paper", "paper"],
    ["from-amber-500/40", "amber-500"],
  ] as const) {
    assert.equal(colourOf(token), colour, token);
  }
  for (const token of [
    "text-sm",
    "text-[11px]",
    "text-2xl",
    "border",
    "border-t",
    "border-2",
    "border-dashed",
    "bg-[#fff]",
    "bg-clip-text",
    "ring-2",
    "outline-none",
    "outline-offset-2",
    "divide-y",
    "to-50%",
    "flex",
    "rounded",
  ]) {
    assert.equal(colourOf(token), null, token);
  }
  // Prose is never read as a class list.
  assert.ok(!"Add a to-do item.".split(/\s+/).every((t) => CLASS_SHAPE.test(t)));
});

test("a template literal's interpolations are scanned, and a default colour needs a shade", () => {
  const sample = 'const c = `rounded ${failed ? "border-warn/30" : "border-line"} px-2`;';
  const bodies = [...stringLiterals(sample, 0)].map((l) => [l.body.trim().replace(/\s+/g, " "), sample.slice(l.index, l.index + 1)]);
  assert.deepEqual(bodies, [
    ["border-warn/30", '"'],
    ["border-line", '"'],
    ["rounded px-2", "`"],
  ]);
  const theme = themeTokens();
  const palette = defaultColours();
  assert.ok(known("amber-500", theme, palette));
  assert.ok(!known("amber", theme, palette), "a bare family generates nothing");
  assert.ok(!known("amber-550", theme, palette), "a shade the palette lacks generates nothing");
  assert.ok(!known("danger", theme, palette));
  assert.ok(known("current", theme, palette));
});

test("every colour utility in the frontend names a colour that exists", () => {
  const theme = themeTokens();
  const palette = defaultColours();
  const found = scan();
  assert.ok(found.length > 500, `expected hundreds of colour utilities, found ${found.length}`);
  const unknown = found.filter((t) => !known(t.colour, theme, palette));
  assert.deepEqual(
    unknown.map((t) => `${t.file}:${t.line} ${t.token}`),
    [],
    "these classes name a colour that is neither an index.css --color-* token nor a Tailwind colour, so Tailwind generates no CSS for them",
  );
});
