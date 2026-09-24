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
import ts from "typescript";

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
    const value = utility.slice(prefix.length + 1).replace(OPACITY, "");
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
const ARBITRARY = String.raw`(?:\[[^\s\]]*\]|\([^\s)]*\))`;
const CLASS_SHAPE = new RegExp(
  String.raw`^[!-]?(?:(?:[a-z0-9-]*${ARBITRARY}|[a-z0-9-]+):)*[!-]?` +
    String.raw`[a-z0-9][a-z0-9.-]*(?:${ARBITRARY}[a-z0-9.-]*)?` +
    String.raw`(?:\/(?:\d+(?:\.\d+)?|${ARBITRARY}))?!?$`,
);
/** An opacity modifier: `/15`, `/7.5`, `/[0.06]`, `/(--alpha)`. */
const OPACITY = new RegExp(String.raw`\/(?:\d+(?:\.\d+)?|${ARBITRARY})$`);
/**
 * Words that have a colour utility's shape but are prose, found by this scan
 * (a one-word string cannot be told from a one-class list by its shape).
 * Keep it short: each entry is a word the scan would otherwise misread.
 */
const PROSE = new Set(["to-do"]);
/**
 * Every string literal in a source file, as its body and the line it starts
 * on — read by the TypeScript parser the build already uses, never by a
 * regex. A regex lexer pairs a stray backtick or apostrophe in a comment or
 * JSX text with the next one and swallows every class string in between.
 * A template literal's body is its text with each `${…}` cut out; the
 * expressions inside are walked like any other code, so a conditional class
 * list (`${failed ? "border-warn" : "border-edge"}`) is scanned too.
 */
function stringLiterals(text: string, fileName = "sample.tsx"): { body: string; line: number }[] {
  const source = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: { body: string; line: number }[] = [];
  const lineOf = (node: ts.Node) => source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
  const visit = (node: ts.Node): void => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      out.push({ body: node.text, line: lineOf(node) });
    } else if (ts.isTemplateExpression(node)) {
      const parts = [node.head.text, ...node.templateSpans.map((span) => span.literal.text)];
      out.push({ body: parts.join(" "), line: lineOf(node) });
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

function scan(): Token[] {
  const found: Token[] = [];
  for (const file of tsxFiles(srcDir)) {
    const text = readFileSync(file, "utf8");
    for (const { body, line } of stringLiterals(text, file)) {
      const tokens = body.split(/\s+/).filter(Boolean);
      if (tokens.length === 0 || !tokens.every((t) => CLASS_SHAPE.test(t))) continue;
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
  // Shapes a class list must keep so it is not skipped whole: decimal
  // spacing, arbitrary opacity, an arbitrary value with an opacity, an
  // arbitrary variant.
  for (const token of [
    "py-1.5",
    "bg-accent/[0.06]",
    "border-[#d4a04c]/60",
    "bg-ok/(--alpha)",
    "[&>p]:text-err",
    "group-[.open]:bg-raised",
    "font-[family-name:var(--font-display)]",
  ]) {
    assert.ok(CLASS_SHAPE.test(token), token);
  }
  assert.equal(colourOf("bg-accent/[0.06]"), "accent");
  assert.equal(colourOf("bg-danger/7.5"), "danger");
  // Prose is never read as a class list.
  assert.ok(!"Add a to-do item.".split(/\s+/).every((t) => CLASS_SHAPE.test(t)));
});

test("literals are read by the parser, interpolations included, and a default colour needs a shade", () => {
  const sample = [
    "// don't let an apostrophe or a `backtick in a comment swallow code",
    'const c = `rounded ${failed ? "border-warn/30" : "border-line"} px-2`;',
    "const d = <p className=\"text-err\">It's `quoted` prose</p>;",
  ].join("\n");
  assert.deepEqual(
    stringLiterals(sample).map((l) => [l.body.replace(/\s+/g, " ").trim(), l.line]),
    [
      ["rounded px-2", 2],
      ["border-warn/30", 2],
      ["border-line", 2],
      ["text-err", 3],
    ],
  );
  const theme = themeTokens();
  const palette = defaultColours();
  assert.ok(known("amber-500", theme, palette));
  assert.ok(!known("amber", theme, palette), "a bare family generates nothing");
  assert.ok(!known("amber-550", theme, palette), "a shade the palette lacks generates nothing");
  assert.ok(!known("danger", theme, palette));
  assert.ok(known("current", theme, palette));
});

test("no class list is skipped by the shape check", () => {
  // A literal holding even one utility that names a real colour is a class
  // list, so every token in it must pass the shape check — otherwise the
  // whole list is skipped and a bad colour beside it goes unseen. (Prose
  // like "to-dos" names no real colour, so it is not caught here.)
  const theme = themeTokens();
  const palette = defaultColours();
  const skipped: string[] = [];
  for (const file of tsxFiles(srcDir)) {
    for (const { body, line } of stringLiterals(readFileSync(file, "utf8"), file)) {
      const tokens = body.split(/\s+/).filter(Boolean);
      const odd = tokens.filter((t) => !CLASS_SHAPE.test(t));
      if (odd.length === 0) continue;
      const colours = tokens.map((t) => colourOf(t)).filter((c): c is string => c !== null);
      if (colours.some((c) => known(c, theme, palette))) {
        skipped.push(`${relative(root, file)}:${line} ${JSON.stringify(odd)}`);
      }
    }
  }
  assert.deepEqual(skipped, [], "these class lists hold tokens the shape check rejects, so the scan skips them");
});

test("every colour utility in the frontend names a colour that exists", () => {
  const theme = themeTokens();
  const palette = defaultColours();
  const found = scan();
  assert.ok(found.length > 2000, `expected hundreds of colour utilities, found ${found.length}`);
  const unknown = found.filter((t) => !known(t.colour, theme, palette));
  assert.deepEqual(
    unknown.map((t) => `${t.file}:${t.line} ${t.token}`),
    [],
    "these classes name a colour that is neither an index.css --color-* token nor a Tailwind colour, so Tailwind generates no CSS for them",
  );
});
