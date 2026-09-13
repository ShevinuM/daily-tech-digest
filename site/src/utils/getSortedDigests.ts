import { getCollection, type CollectionEntry } from 'astro:content';

/** All digests, newest first (by collection id, which is the `YYYY-MM-DD` filename stem). */
export async function getSortedDigests(): Promise<CollectionEntry<'digests'>[]> {
	const digests = await getCollection('digests');
	return digests.sort((a, b) => b.id.localeCompare(a.id));
}
