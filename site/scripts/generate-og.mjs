#!/usr/bin/env node
/**
 * Regenerates public/og.png — the social preview card used on every page.
 *
 * Run by hand after changing SITE.title/description or the theme colours:
 *
 *   node scripts/generate-og.mjs
 *
 * Deliberately NOT part of `npm run build`. The card is static, so it is
 * rendered once and committed; keeping it out of the build means CI needs no
 * font packages installed (the SVG below resolves a system monospace face,
 * which differs between macOS and the Ubuntu runner).
 *
 * `sharp` is already present transitively via astro's image service.
 */
import sharp from 'sharp';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../public/og.png');

// Light-theme tokens, kept in sync with src/styles/theme.css by hand.
const BG = '#fdfdfd';
const FG = '#282728';
const ACCENT = '#006cac';
const MUTED = '#6b7280';
const BORDER = '#ece9e9';

// Mirrors --font-app from src/styles/theme.css.
const MONO = 'Menlo, ui-monospace, SFMono-Regular, Consolas, monospace';

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="${BG}"/>
  <rect x="64" y="56" width="1072" height="518" fill="none" stroke="${BORDER}" stroke-width="2"/>
  <rect x="64" y="56" width="1072" height="6" fill="${ACCENT}"/>

  <text x="112" y="212" font-family="${MONO}" font-size="76" font-weight="700" fill="${FG}">Shevinu&#8217;s Digest</text>
  <rect x="112" y="248" width="220" height="4" fill="${ACCENT}"/>

  <text x="112" y="330" font-family="${MONO}" font-size="34" fill="${MUTED}">A daily digest of tech reading &#8212;</text>
  <text x="112" y="380" font-family="${MONO}" font-size="34" fill="${MUTED}">fetched, ranked, and summarized</text>
  <text x="112" y="430" font-family="${MONO}" font-size="34" fill="${MUTED}">on a schedule.</text>

  <text x="1088" y="526" font-family="${MONO}" font-size="30" font-weight="700" fill="${ACCENT}" text-anchor="end">digest.shevinum.dev</text>
</svg>`;

await sharp(Buffer.from(svg)).png().toFile(OUT);
console.log(`wrote ${OUT}`);
