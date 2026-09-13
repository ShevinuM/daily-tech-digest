/**
 * Single source of brand truth for the site. Import this instead of
 * hardcoding the site name, description, or social links elsewhere.
 */

export const SITE = {
	title: "Shevinu's Digest",
	author: "Shevinu",
	url: "https://digest.shevinum.dev",
	description:
		"A daily digest of tech reading — fetched, ranked, and summarized on a schedule.",
	lang: "en",
	timezone: "UTC",
} as const;

export interface SocialLink {
	name: string;
	url: string;
	linkTitle?: string;
}

export const SOCIALS: SocialLink[] = [
	{
		name: 'github',
		url: 'https://github.com/ShevinuM/daily-tech-digest',
		linkTitle: `${SITE.title} on GitHub`,
	},
];
