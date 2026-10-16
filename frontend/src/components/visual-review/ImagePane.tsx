import { ExternalLink, ImageOff } from 'lucide-react';

/** One labelled screenshot (baseline / actual / difference), opens full size. */
export function ImagePane({ label, hint, url }: { label: string; hint: string; url: string | null }) {
    return (
        <figure className="flex min-w-0 flex-col gap-2">
            <figcaption className="flex flex-col">
                <span className="text-sm font-semibold text-ink">{label}</span>
                <span className="text-xs text-ink-soft">{hint}</span>
            </figcaption>
            {url ? (
                <a href={url} target="_blank" rel="noreferrer" className="group relative block rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`Open ${label.toLowerCase()} image in a new tab`}>
                    <img
                        src={url}
                        alt={label}
                        className="max-h-[420px] w-full rounded-2xl border border-border bg-white object-contain"
                    />
                    <span className="absolute right-2 top-2 rounded-full bg-white/95 p-1.5 opacity-0 shadow-sm transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden="true">
                        <ExternalLink className="h-3.5 w-3.5 text-ink" />
                    </span>
                </a>
            ) : (
                <div className="flex h-40 w-full flex-col items-center justify-center gap-1.5 rounded-2xl border border-dashed border-border bg-background text-ink-soft">
                    <ImageOff className="h-5 w-5" aria-hidden="true" />
                    <span className="text-xs">Not available</span>
                </div>
            )}
        </figure>
    );
}
