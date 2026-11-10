// Class strings shared by the operations pages (kept apart from bits.tsx for fast refresh).

// Table classes, so plain tables inside white panels read like the rest of the redesign.
export const tbl = {
    table: 'w-full text-left text-sm',
    headRow: 'border-b border-[#F0EEE8]',
    th: 'whitespace-nowrap px-4 py-3 text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6',
    row: 'border-b border-[#F0EEE8] align-top last:border-b-0',
    td: 'px-4 py-3 text-[13px] text-ink first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6',
};

export const fieldLabel = 'text-[13px] font-semibold text-ink';
export const nativeSelect = 'h-10 rounded-full border border-input bg-white px-4 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-ink/20';
export const dangerOutline = 'text-pastel-ink-peach hover:bg-pastel-peach hover:text-pastel-ink-peach';
