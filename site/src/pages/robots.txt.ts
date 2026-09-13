import type { APIRoute } from 'astro';
import { SITE } from '../config';

export const GET: APIRoute = ({ site }) => {
	const sitemapURL = new URL('sitemap-index.xml', site ?? SITE.url).href;
	const body = `User-agent: *\nAllow: /\n\nSitemap: ${sitemapURL}\n`;

	return new Response(body, {
		headers: { 'Content-Type': 'text/plain; charset=utf-8' },
	});
};
