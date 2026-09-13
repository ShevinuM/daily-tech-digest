/**
 * Trailing-slash-safe base-path join for internal hrefs.
 *
 * `import.meta.env.BASE_URL` is `/` at the custom-domain root. Naively
 * concatenating `${base}${path}` when both already carry a slash produces
 * `//path`, which browsers resolve as a protocol-relative URL to a
 * *different host*, not a same-site path. This always normalizes to
 * exactly one leading slash and no doubled separator.
 */
export function withBase(path: string): string {
	const base = import.meta.env.BASE_URL.replace(/\/$/, '');
	const normalizedPath = path.startsWith('/') ? path : `/${path}`;
	return base ? `${base}${normalizedPath}` : normalizedPath;
}
