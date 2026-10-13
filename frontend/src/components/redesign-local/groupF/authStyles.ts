// Shared class strings for the signed-out (auth) pages. Kept in a .ts file so
// the component module only exports components (react-refresh rule).

export const authInput =
    'h-11 w-full rounded-full border border-border bg-white px-4 text-sm text-ink placeholder:text-ink-muted shadow-sm transition-colors ' +
    'focus:outline-none focus-visible:border-ink focus-visible:ring-2 focus-visible:ring-ink/25 disabled:cursor-not-allowed disabled:opacity-60';

export const authPrimary =
    'inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-full bg-ink px-5 text-sm font-semibold text-white transition-colors ' +
    'hover:bg-ink/85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60';

export const authOutline =
    'inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-full border border-border bg-white px-5 text-sm font-semibold text-ink transition-colors ' +
    'hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2';

export const authLink =
    'rounded-sm font-semibold text-ink underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink';
