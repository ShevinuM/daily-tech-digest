import { getSortedDigests } from './getSortedDigests';
import { slugifyStr } from './slugify';

export interface TaggedItem {
	url: string;
	title: string;
	source: string;
	publishedAt: string;
	tags: string[];
	summary: string;
}

export interface DigestTagGroup {
	/** Digest collection id (`YYYY-MM-DD`). */
	date: string;
	title: string;
	items: TaggedItem[];
}

/** Items carrying the given tag slug, grouped by digest date, newest first. */
export async function getItemsByTag(tagSlug: string): Promise<DigestTagGroup[]> {
	const digests = await getSortedDigests();
	const groups: DigestTagGroup[] = [];

	for (const digest of digests) {
		const items: TaggedItem[] = [];
		for (const section of digest.data.sections) {
			for (const item of section.items) {
				if (item.tags.some((tagName) => slugifyStr(tagName) === tagSlug)) {
					items.push(item);
				}
			}
		}
		if (items.length > 0) {
			groups.push({ date: digest.id, title: digest.data.title, items });
		}
	}

	return groups;
}
