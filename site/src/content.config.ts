import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const digests = defineCollection({
	loader: glob({ pattern: '**/*.json', base: './src/content/digests' }),
	schema: z.object({
		date: z.string(),
		title: z.string(),
		generatedAt: z.string(),
		intro: z.string(),
		sections: z.array(
			z.object({
				heading: z.string(),
				items: z.array(
					z.object({
						url: z.string(),
						title: z.string(),
						source: z.string(),
						publishedAt: z.string(),
						tags: z.array(z.string()).default([]),
						summary: z.string(),
					}),
				),
			}),
		),
		stats: z.object({
			itemCount: z.number(),
			sourcesScanned: z.array(z.string()),
			errors: z.array(z.string()),
		}),
	}),
});

export const collections = { digests };
