# site

The Astro front end for [daily-tech-digest](..) — a port of the
[AstroPaper](https://github.com/satnaing/astro-paper) v6 theme (Tailwind v4
design tokens, dark/light toggle, tags, archives, Pagefind search) over the
digest content the pipeline writes to `src/content/digests/*.json`. A
homepage with the latest digest, a full `/archive/`, one page per day at
`/digest/<date>/`, `/tags/` pages, and `/search/`. Deployed to GitHub Pages
by `.github/workflows/digest.yml`. See `THEME-LICENSE` for the AstroPaper
MIT notice this port is required to retain.

## Layout

```
astro.config.mjs           site/base for the GitHub Pages project URL,
                            Tailwind v4 (@tailwindcss/vite) + @astrojs/sitemap
src/config.ts               single source of brand truth (SITE, SOCIALS)
src/content.config.ts       the `digests` collection schema (JSON data loader)
src/content/digests/        one <date>.json per day, written by the pipeline
src/styles/                 global.css, theme.css (design tokens, dark mode),
                            typography.css
src/layouts/Layout.astro    shared page shell: head/meta, theme script,
                            view transitions
src/components/
  Header.astro Footer.astro   site chrome, nav, theme-toggle
  Card.astro                  one digest summary in a list
  Datetime.astro               UTC date formatting
  Tag.astro                    a tag pill linking to /tags/<slug>/
  Main.astro                   <main> wrapper (pageTitle/pageDesc)
  DigestSections.astro         renders a digest's sections/items; the
                                homepage copy is data-pagefind-ignore so
                                Pagefind indexes only the canonical
                                /digest/<date>/ page
src/utils/
  getSortedDigests.ts   getUniqueTags.ts   getItemsByTag.ts
  slugify.ts (lodash.kebabcase wrapper)   withBase.ts
src/scripts/theme.ts        localStorage + prefers-color-scheme toggle
scripts/generate-og.mjs     regenerates public/og.png (run by hand, not
                            part of the build)
public/og.png               the social preview card, referenced as
                            og:image/twitter:image on every page
src/pages/
  index.astro                latest digest + recent list
  archive/index.astro        every digest, grouped by year/month
  digest/[date].astro        one specific day
  tags/index.astro           every tag with its count
  tags/[tag].astro           items for one tag, grouped by digest date
  search.astro               Pagefind UI
  404.astro
  robots.txt.ts
tests/smoke.mjs              zero-dependency post-build smoke test
```

## Commands

```sh
npm install
npm run dev       # localhost:4321
npm run build     # astro check && astro build && pagefind --site dist
                   # (also copies dist/pagefind -> public/ so `astro dev`
                   # can serve search)
npm run preview
npm test          # node tests/smoke.mjs — run AFTER `npm run build`

node scripts/generate-og.mjs   # regenerate public/og.png, then commit it
```

The social preview card (`public/og.png`) is static and committed, not built.
Regenerate it only after changing `SITE.title`/`SITE.description` or the theme
colours. It is deliberately outside `npm run build` so CI needs no font
packages — the card's SVG resolves a system monospace face, which differs
between macOS and the Ubuntu runner.

There's nothing to configure locally — drop a `src/content/digests/YYYY-MM-DD.json`
matching the schema in `src/content.config.ts` to preview a digest.

## Smoke test

`npm test` runs `tests/smoke.mjs` against the built `dist/` output: required
pages exist (including a `tags/<slug>/` page for every distinct tag and the
Pagefind assets), the old brand name is absent, the header/`<title>` carry
the current `SITE.title`, the theme toggle is present on every page, and
every item/tag from each source JSON is actually rendered with the right
item count. Run `npm run build` first — the test reads `dist/`, not live
Astro output.
