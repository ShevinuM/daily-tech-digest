#!/usr/bin/env node
// Zero-dependency smoke test for the built site in dist/. Run AFTER `npm run
// build`. Recomputes expected tags/items/counts from the source JSON in
// src/content/digests/ — nothing here is a hardcoded number.
//
// Wired as `npm test` (site/package.json: "test": "node tests/smoke.mjs").

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SITE_ROOT = path.resolve(__dirname, '..');
const DIST = path.join(SITE_ROOT, 'dist');
const DIGESTS_DIR = path.join(SITE_ROOT, 'src/content/digests');
const REPO_ROOT = path.resolve(SITE_ROOT, '..');

let passCount = 0;

function fail(message) {
	console.error(`FAIL: ${message}`);
	process.exit(1);
}

function ok(message) {
	passCount += 1;
	void message; // kept for readability at call sites; not printed per-assertion
}

function assert(condition, message) {
	if (!condition) fail(message);
	ok(message);
}

/** Decode the handful of HTML entities Astro's renderer actually emits. */
function unescapeHtml(str) {
	return str
		.replace(/&#x([0-9a-fA-F]+);/g, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
		.replace(/&#(\d+);/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10)))
		.replace(/&amp;/g, '&')
		.replace(/&lt;/g, '<')
		.replace(/&gt;/g, '>')
		.replace(/&quot;/g, '"')
		.replace(/&#39;/g, "'")
		.replace(/&apos;/g, "'");
}

/** Minimal kebab-case, sufficient for (and cross-checked against) our tag set. */
function kebabCase(str) {
	return str
		.replace(/([a-z0-9])([A-Z])/g, '$1-$2')
		.toLowerCase()
		.replace(/[\s_]+/g, '-')
		.replace(/[^a-z0-9-]+/g, '-')
		.replace(/-+/g, '-')
		.replace(/^-|-$/g, '');
}

function readFile(relPathFromDist) {
	const full = path.join(DIST, relPathFromDist);
	if (!fs.existsSync(full)) fail(`missing expected file: dist/${relPathFromDist}`);
	return fs.readFileSync(full, 'utf-8');
}

function findAllHtmlFiles(dir) {
	const out = [];
	for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
		const full = path.join(dir, entry.name);
		if (entry.isDirectory()) {
			out.push(...findAllHtmlFiles(full));
		} else if (entry.name.endsWith('.html')) {
			out.push(full);
		}
	}
	return out;
}

// ---------------------------------------------------------------------------
// Load source JSON and recompute everything the test needs to check against.
// ---------------------------------------------------------------------------

if (!fs.existsSync(DIGESTS_DIR)) fail(`missing source directory: ${DIGESTS_DIR}`);

const digestFiles = fs
	.readdirSync(DIGESTS_DIR)
	.filter((f) => f.endsWith('.json'))
	.sort();

if (digestFiles.length === 0) fail('no source digest JSON files found — nothing to verify');

const digests = digestFiles.map((f) => {
	const raw = fs.readFileSync(path.join(DIGESTS_DIR, f), 'utf-8');
	const data = JSON.parse(raw);
	return { file: f, data };
});

const allTagNames = new Set();
for (const { data } of digests) {
	for (const section of data.sections) {
		for (const item of section.items) {
			for (const tag of item.tags ?? []) allTagNames.add(tag);
		}
	}
}
const expectedTagSlugs = new Set([...allTagNames].map(kebabCase));

// ---------------------------------------------------------------------------
// 1. Required files exist.
// ---------------------------------------------------------------------------

const requiredFiles = [
	'index.html',
	'archive/index.html',
	'404.html',
	'tags/index.html',
	'search/index.html',
	'robots.txt',
	'sitemap-index.xml',
	'og.png',
	'pagefind/pagefind.js',
];
for (const rel of requiredFiles) {
	assert(fs.existsSync(path.join(DIST, rel)), `required file exists: dist/${rel}`);
}
for (const { data } of digests) {
	const rel = `digest/${data.date}/index.html`;
	assert(fs.existsSync(path.join(DIST, rel)), `digest page exists: dist/${rel}`);
}

// ---------------------------------------------------------------------------
// 2. A tags/<slug>/index.html exists for every distinct tag in the source JSON,
//    and (set equality) there are no extra/missing tag pages beyond those.
// ---------------------------------------------------------------------------

const tagsDir = path.join(DIST, 'tags');
const builtTagSlugs = new Set(
	fs
		.readdirSync(tagsDir, { withFileTypes: true })
		.filter((e) => e.isDirectory())
		.map((e) => e.name),
);

for (const slug of expectedTagSlugs) {
	assert(builtTagSlugs.has(slug), `tag page exists: dist/tags/${slug}/index.html`);
}
for (const slug of builtTagSlugs) {
	assert(expectedTagSlugs.has(slug), `no unexpected tag page: dist/tags/${slug}/ (not in source JSON)`);
}
assert(
	expectedTagSlugs.size === builtTagSlugs.size,
	`tag page count matches source JSON distinct-tag count (${expectedTagSlugs.size})`,
);

// ---------------------------------------------------------------------------
// Walk every built HTML page for the page-wide assertions (3, 4, 5, 7).
// ---------------------------------------------------------------------------

const allHtmlFiles = findAllHtmlFiles(DIST);
assert(allHtmlFiles.length > 0, 'at least one built HTML page found');

const FORBIDDEN_STRINGS = ['Tech Reading Digest', 'Daily Tech Digest', 'AstroPaper'];
const BRAND = "Shevinu's Digest";
const SITE_URL = 'https://digest.shevinum.dev';

for (const fullPath of allHtmlFiles) {
	const relPath = path.relative(DIST, fullPath);
	const raw = fs.readFileSync(fullPath, 'utf-8');
	const html = unescapeHtml(raw);

	// 3. No stale brand name anywhere.
	for (const forbidden of FORBIDDEN_STRINGS) {
		assert(!html.includes(forbidden), `dist/${relPath} does not contain "${forbidden}"`);
	}

	// 4. <title> contains the new brand.
	const titleMatch = html.match(/<title>([^<]*)<\/title>/);
	assert(Boolean(titleMatch), `dist/${relPath} has a <title> tag`);
	assert(titleMatch[1].includes(BRAND), `dist/${relPath} <title> contains "${BRAND}"`);

	// 4b. <title> does not contain the brand more than once (regression guard
	// for digest titles that already begin with SITE.title getting " | SITE.title"
	// appended on top).
	const brandOccurrences = titleMatch[1].split(BRAND).length - 1;
	assert(
		brandOccurrences <= 1,
		`dist/${relPath} <title> contains "${BRAND}" at most once (got ${brandOccurrences}): ${JSON.stringify(titleMatch[1])}`,
	);

	// 5. Theme toggle present on every page.
	assert(raw.includes('id="theme-btn"'), `dist/${relPath} contains id="theme-btn"`);

	// 7. No protocol-relative internal hrefs.
	assert(!/href="\/\//.test(raw), `dist/${relPath} has no href="//..." (protocol-relative URL)`);

	// 9. Social preview card is advertised on every page, as an absolute URL —
	// crawlers do not resolve a relative og:image against the page.
	const ogImageMatch = raw.match(/<meta property="og:image" content="([^"]*)"/);
	assert(Boolean(ogImageMatch), `dist/${relPath} has an og:image meta tag`);
	assert(
		ogImageMatch[1] === `${SITE_URL}/og.png`,
		`dist/${relPath} og:image is ${SITE_URL}/og.png (got ${JSON.stringify(ogImageMatch[1])})`,
	);
	assert(
		raw.includes('<meta property="twitter:card" content="summary_large_image"'),
		`dist/${relPath} uses twitter:card=summary_large_image (required for the image to show)`,
	);
}

// 4 (continued). Header brand text is exactly "Shevinu's Digest" (checked on
// the homepage, where the brand link points at "/").
{
	const homeHtml = unescapeHtml(readFile('index.html'));
	const brandMatch = homeHtml.match(/<a href="\/"[^>]*>([^<]*)<\/a>/);
	assert(Boolean(brandMatch), 'homepage has a brand link <a href="/">...</a>');
	assert(brandMatch[1] === BRAND, `header brand text is exactly "${BRAND}" (got "${brandMatch[1]}")`);
}

// ---------------------------------------------------------------------------
// 6. Per-digest-page checks: every item url + tag appears, item count matches.
// ---------------------------------------------------------------------------

for (const { file, data } of digests) {
	const relPath = `digest/${data.date}/index.html`;
	const raw = readFile(relPath);
	const html = unescapeHtml(raw);

	const items = data.sections.flatMap((s) => s.items);

	for (const item of items) {
		assert(html.includes(item.url), `dist/${relPath} renders item url ${item.url} (from ${file})`);
		for (const tag of item.tags ?? []) {
			assert(html.includes(tag), `dist/${relPath} renders tag "${tag}" (from ${file})`);
		}
	}

	const renderedItemCount = (raw.match(/data-digest-item/g) ?? []).length;
	assert(
		renderedItemCount === data.stats.itemCount,
		`dist/${relPath} renders ${data.stats.itemCount} data-digest-item entries (from ${file}'s stats.itemCount), got ${renderedItemCount}`,
	);
}

// ---------------------------------------------------------------------------
// 7b. Pagefind index integrity: no homepage ("/") fragment, no duplicate
//    urls, and exactly one fragment per digest page. Guards against a stale
//    index left over from a prior build (astro build copies public/pagefind
//    into dist/pagefind before pagefind indexes, so an unclean build script
//    can leave old fragments sitting alongside the fresh ones).
// ---------------------------------------------------------------------------

const fragmentDir = path.join(DIST, 'pagefind', 'fragment');
assert(fs.existsSync(fragmentDir), `pagefind fragment directory exists: dist/pagefind/fragment`);

function readFragmentUrl(fullPath) {
	const raw = fs.readFileSync(fullPath);
	let text;
	try {
		text = gunzipSync(raw).toString('utf-8');
	} catch {
		text = raw.toString('utf-8');
	}
	// Pagefind prefixes each fragment's JSON payload with a short opaque
	// marker (e.g. "pagefind_dcd") before the opening brace — strip up to the
	// first "{" rather than assuming the payload is pure JSON.
	const jsonStart = text.indexOf('{');
	if (jsonStart === -1) fail(`fragment ${fullPath} does not contain a JSON payload`);
	const parsed = JSON.parse(text.slice(jsonStart));
	return parsed.url;
}

const fragmentFiles = fs.readdirSync(fragmentDir).filter((f) => f.endsWith('.pf_fragment'));
const fragmentUrls = fragmentFiles.map((f) => readFragmentUrl(path.join(fragmentDir, f)));

const expectedDigestUrls = new Set(digests.map(({ data }) => `/digest/${data.date}/`));

// Checked first and in isolation so a regression here fails with an
// unambiguous message naming the homepage, rather than being buried inside a
// generic fragment-count mismatch.
assert(
	!fragmentUrls.includes('/'),
	`pagefind index has no fragment for "/" (the homepage must not be indexed — step 11b), got urls: ${JSON.stringify(fragmentUrls)}`,
);

assert(
	new Set(fragmentUrls).size === fragmentUrls.length,
	`pagefind index has no duplicate fragment urls, got: ${JSON.stringify(fragmentUrls)}`,
);

for (const url of expectedDigestUrls) {
	assert(fragmentUrls.includes(url), `pagefind index has a fragment for ${url}`);
}

assert(
	fragmentUrls.length === expectedDigestUrls.size,
	`pagefind index has exactly ${expectedDigestUrls.size} fragment(s) (one per digest page), got ${fragmentUrls.length}: ${JSON.stringify(fragmentUrls)}`,
);

// ---------------------------------------------------------------------------
// 8. Repo-level brand check.
// ---------------------------------------------------------------------------

const configPath = path.join(REPO_ROOT, 'config.json');
assert(fs.existsSync(configPath), `repo config.json exists at ${configPath}`);
const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
assert(
	config?.site?.title === BRAND,
	`../config.json site.title === "${BRAND}" (got ${JSON.stringify(config?.site?.title)})`,
);

console.log(`smoke test passed: ${passCount} assertions OK`);
