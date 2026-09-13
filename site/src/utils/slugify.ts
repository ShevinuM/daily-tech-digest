import kebabCase from 'lodash.kebabcase';

/** Slugify a display string (e.g. a tag name) for use in a URL segment. */
export function slugifyStr(str: string): string {
	return kebabCase(str);
}
