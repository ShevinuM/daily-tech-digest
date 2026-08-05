// @ts-check
import { defineConfig } from 'astro/config';

// Custom domain, served from the root (not a /daily-tech-digest subpath) —
// digest.shevinum.dev is a dedicated subdomain, since www.shevinum.dev is
// already the portfolio site's custom domain and a domain can only point
// at one Pages deployment.
export default defineConfig({
	site: 'https://digest.shevinum.dev',
	base: '/',
});
