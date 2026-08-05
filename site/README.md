# site

The Astro front end for [daily-tech-digest](..). Renders the digests the
pipeline writes to `src/content/digests/*.json` — a homepage with the
latest digest, a full `/archive/`, and one page per day at `/digest/<date>/`.
Deployed to GitHub Pages by `.github/workflows/digest.yml`.

## Layout

```
astro.config.mjs         site/base set for the GitHub Pages project URL
src/content.config.ts    the `digests` collection schema (JSON data loader)
src/content/digests/     one <date>.json per day, written by the pipeline
src/layouts/Layout.astro shared page shell + styles
src/components/          DigestBody.astro renders one digest's sections/items
src/pages/
  index.astro             latest digest
  archive/index.astro    every digest, newest first
  digest/[date].astro    one specific day
```

## Commands

```sh
npm install
npm run dev       # localhost:4321
npm run build     # -> dist/
npm run preview
```

There's nothing to configure locally — drop a `src/content/digests/YYYY-MM-DD.json`
matching the schema in `src/content.config.ts` to preview a digest.
