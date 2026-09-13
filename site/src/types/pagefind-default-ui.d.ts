/**
 * `@pagefind/default-ui` ships no type declarations (it targets plain
 * bundlers, not TS consumers). Declare just enough of its shape for the
 * search page's usage.
 */
declare module '@pagefind/default-ui' {
	interface PagefindUIOptions {
		element: string;
		bundlePath?: string;
		showSubResults?: boolean;
		showImages?: boolean;
		[key: string]: unknown;
	}

	export class PagefindUI {
		constructor(options: PagefindUIOptions);
	}
}

declare module '@pagefind/default-ui/css/ui.css';
