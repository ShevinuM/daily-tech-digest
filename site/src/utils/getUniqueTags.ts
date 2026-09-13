import { getSortedDigests } from './getSortedDigests';
import { slugifyStr } from './slugify';

export interface TagInfo {
	/** URL-safe slug, used as the `/tags/<tag>/` path segment. */
	tag: string;
	/** Original display string, as it appears in the source JSON. */
	tagName: string;
	/** Number of items across all digests carrying this tag. */
	count: number;
}

/**
 * Every distinct tag across all digests, deduped by slug (so e.g. "CI" and
 * "ci" collapse into one entry) and sorted alphabetically by display name.
 */
export async function getUniqueTags(): Promise<TagInfo[]> {
	const digests = await getSortedDigests();
	const tags = new Map<string, TagInfo>();

	for (const digest of digests) {
		for (const section of digest.data.sections) {
			for (const item of section.items) {
				for (const tagName of item.tags) {
					const tag = slugifyStr(tagName);
					const existing = tags.get(tag);
					if (existing) {
						existing.count += 1;
					} else {
						tags.set(tag, { tag, tagName, count: 1 });
					}
				}
			}
		}
	}

	return [...tags.values()].sort((a, b) => a.tagName.localeCompare(b.tagName));
}
