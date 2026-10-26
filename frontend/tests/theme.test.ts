/**
 * The colour schemes (lib/theme.ts, copied from Spec Surgeon) against the
 * stylesheet that draws them: every scheme redefines every colour token, the
 * picker's swatches are the scheme's own colours, the query string carries
 * the choice, index.html applies it before the first paint, and nothing on
 * the chrome names a colour of its own (a literal would ignore the scheme).
 *
 * The paper keeps literal status colours (diff marks, provision badges): it
 * is near-white in every scheme, so they keep their meaning there. The rules
 * below are about the chrome's tokens only.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import {
  DEFAULT_THEME,
  THEMES,
  resolveTheme,
  searchWithTheme,
  themeFromSearch,
} from "../src/lib/theme.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const CSS = read("../src/index.css");

/** `--color-name: value;` pairs of one block's body. */
function tokens(body: string): Map<string, string> {
  return new Map(
    [...body.matchAll(/--color-([a-z-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]),
  );
}

function block(selector: string): string {
  const start = CSS.indexOf(`${selector} {`);
  assert.notEqual(start, -1, `no ${selector} block in index.css`);
  return CSS.slice(start, CSS.indexOf("}", start));
}

const BASE = tokens(block("@theme"));

function schemeTokens(id: string): Map<string, string> {
  return id === DEFAULT_THEME ? BASE : tokens(block(`:root[data-theme="${id}"]`));
}

/** index.css with the @theme block and every scheme block cut out. */
function cssOutsideSchemes(): string {
  let rest = CSS;
  for (const selector of [
    "@theme",
    ...THEMES.filter((t) => t.id !== DEFAULT_THEME).map((t) => `:root[data-theme="${t.id}"]`),
  ]) {
    const start = rest.indexOf(`${selector} {`);
    rest = rest.slice(0, start) + rest.slice(rest.indexOf("}", start) + 1);
  }
  return rest;
}

/**
 * The chrome's own colours in Warm Clay, as hex and as an `r, g, b` triple.
 * Not ok/warn/err: the paper's badges and diff marks share those hues.
 */
const CHROME_TOKENS = [
  "bg",
  "surface",
  "raised",
  "edge",
  "ink",
  "ink-dim",
  "ink-faint",
  "accent",
  "accent-hover",
];
function chromeLiterals(): { hex: string; rgb: RegExp }[] {
  return CHROME_TOKENS.map((name) => {
    const hex = BASE.get(name)!.toLowerCase();
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    return { hex, rgb: new RegExp(`rgba?\\(\\s*${r}\\s*,\\s*${g}\\s*,\\s*${b}\\b`) };
  });
}

function tsxSources(dir: URL): { name: string; text: string }[] {
  const out: { name: string; text: string }[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const url = new URL(entry.name + (entry.isDirectory() ? "/" : ""), dir);
    if (entry.isDirectory()) out.push(...tsxSources(url));
    else if (entry.name.endsWith(".tsx")) out.push({ name: entry.name, text: readFileSync(url, "utf8") });
  }
  return out;
}
const TSX = tsxSources(new URL("../src/", import.meta.url));

test("the schemes have unique ids and Warm Clay comes first, as the default", () => {
  assert.equal(new Set(THEMES.map((t) => t.id)).size, THEMES.length);
  assert.equal(DEFAULT_THEME, "clay");
  assert.deepEqual(
    THEMES.map((t) => t.id),
    ["clay", "blueprint", "graphite", "carbon", "linen"],
  );
});

test("every other scheme redefines every colour token of @theme, and only those", () => {
  assert.ok(BASE.size >= 18);
  assert.ok(BASE.has("on-accent") && BASE.has("imported"));
  for (const t of THEMES.filter((t) => t.id !== DEFAULT_THEME)) {
    const own = schemeTokens(t.id);
    assert.deepEqual([...own.keys()].sort(), [...BASE.keys()].sort(), t.id);
    assert.match(block(`:root[data-theme="${t.id}"]`), /color-scheme:\s*(dark|light);/, t.id);
  }
  const blocks = [...CSS.matchAll(/:root\[data-theme="([a-z]+)"\]/g)].map((m) => m[1]);
  assert.deepEqual(
    blocks.sort(),
    THEMES.filter((t) => t.id !== DEFAULT_THEME).map((t) => t.id).sort(),
  );
});

test("each card in the picker shows its scheme's own background, paper and accent", () => {
  for (const t of THEMES) {
    const own = schemeTokens(t.id);
    assert.deepEqual(
      t.swatch,
      { bg: own.get("bg"), paper: own.get("paper"), accent: own.get("accent") },
      t.id,
    );
  }
});

test("the query string's theme is read, defaulted and replaced without losing the rest", () => {
  assert.equal(themeFromSearch("?theme=carbon"), "carbon");
  assert.equal(themeFromSearch("?x=1"), DEFAULT_THEME);
  assert.equal(themeFromSearch("theme=dark"), DEFAULT_THEME);
  assert.equal(resolveTheme(7), DEFAULT_THEME);
  assert.equal(resolveTheme("linen"), "linen");
  const next = new URLSearchParams(searchWithTheme("?x=1&theme=clay", "blueprint"));
  assert.equal(next.get("x"), "1");
  assert.equal(next.get("theme"), "blueprint");
  assert.equal(new URLSearchParams(searchWithTheme("", "linen")).get("theme"), "linen");
});

test("index.html applies the scheme before the first paint, before the app's own script", () => {
  const html = read("../index.html");
  assert.match(html, /<meta name="color-scheme" content="dark" \/>/);
  const inline = html.indexOf('get("theme")');
  assert.ok(inline > 0, "no inline ?theme= script");
  assert.match(html, /document\.documentElement\.setAttribute\("data-theme", theme\)/);
  assert.ok(inline < html.indexOf("/src/main.tsx"), "the scheme must be set before the app loads");
  assert.ok(inline < html.indexOf("</head>"), "the scheme must be set in <head>");
});

test("the chrome's colours outside the scheme blocks come from the tokens", () => {
  const rest = cssOutsideSchemes().toLowerCase();
  for (const { hex, rgb } of chromeLiterals()) {
    assert.ok(!rest.includes(hex), `index.css names ${hex} outside the scheme blocks`);
    assert.doesNotMatch(rest, rgb, `index.css names ${hex} as rgb() outside the scheme blocks`);
  }
});

test("no component names a chrome colour of its own or follows the OS's dark mode", () => {
  const literals = chromeLiterals();
  for (const { name, text } of TSX) {
    const lower = text.toLowerCase();
    for (const { hex, rgb } of literals) {
      assert.ok(!lower.includes(hex), `${name} names ${hex}; use its token`);
      assert.doesNotMatch(lower, rgb, `${name} names ${hex} as rgb(); use its token`);
    }
    // Tailwind's `dark:` follows the operating system, not the app's scheme.
    assert.doesNotMatch(text, /(^|[\s"'`])dark:/m, `${name} uses dark:`);
  }
});

test("text on an accent fill is on-accent, which is dark on the bright accents", () => {
  for (const { name, text } of TSX) {
    // Each quoted string on its own, and each template's static text without
    // its `${…}` parts: a ternary's branches are separate class lists.
    const templates = (text.match(/`[^`]*`/g) ?? []).map((t) => t.replace(/\$\{[^}]*\}/g, " "));
    const strings = text.match(/"[^"\n]*"|'[^'\n]*'/g) ?? [];
    for (const literal of [...templates, ...strings]) {
      const classes = literal.split(/[\s"'`]+/);
      if (classes.includes("bg-accent") && classes.includes("text-white")) {
        assert.fail(`${name}: ${literal.slice(0, 120)} puts text-white on bg-accent`);
      }
    }
  }
  assert.equal(BASE.get("on-accent"), "#ffffff");
  assert.notEqual(schemeTokens("carbon").get("on-accent"), "#ffffff");
});

test("Settings → Appearance is a radio group of every scheme, wired to App's picker", () => {
  const settings = read("../src/components/SettingsPanel.tsx");
  assert.match(settings, /data-capability="session\.appearance"/);
  assert.match(settings, /role="radiogroup"\s+aria-label="Color scheme"/);
  assert.match(settings, /THEMES\.map\(/);
  assert.match(settings, /role="radio"\s+aria-checked=\{chosen\}/);
  assert.match(settings, /onClick=\{\(\) => onPickTheme\(t\.id\)\}/);
  assert.match(settings, /themeError && \(/);
  const app = read("../src/App.tsx");
  assert.match(app, /const colorScheme = useColorScheme\(\);/);
  assert.match(app, /onPickTheme=\{colorScheme\.pick\}/);
  // A pick the server did not keep is taken back, with its reason.
  const hook = read("../src/lib/useColorScheme.ts");
  assert.match(hook, /applyTheme\(previous\);\s*setTheme\(previous\);\s*setError\(/);
  assert.match(hook, /if \(mine !== turn\.current\) return;/);
});
