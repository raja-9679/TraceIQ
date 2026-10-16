// Schedule time helpers. The backend stores and returns naive UTC timestamps
// ("2026-10-19T03:30:00", no "Z"); `new Date()` would read those as LOCAL
// time, so parse them as UTC explicitly.

const HAS_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i;

export function parseServerDate(value: string): Date {
    return new Date(HAS_ZONE.test(value) ? value : `${value}Z`);
}

// Next/last run in the viewer's own clock, e.g. "Oct 19, 09:00 AM".
export function formatScheduleTime(value?: string | null): string {
    if (!value) return 'Never';
    return parseServerDate(value).toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
}

export function browserTimezone(): string {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch {
        return 'UTC';
    }
}

// Fallback for browsers without Intl.supportedValuesOf: the zones teams most
// often schedule in. Any IANA name typed by hand is still accepted (the
// backend validates it).
const COMMON_ZONES = [
    'UTC', 'Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Asia/Tokyo',
    'Australia/Sydney', 'Europe/London', 'Europe/Berlin', 'Europe/Paris',
    'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
    'America/Sao_Paulo', 'Africa/Johannesburg',
];

export function listTimezones(): string[] {
    const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] })
        .supportedValuesOf;
    let zones: string[] = COMMON_ZONES;
    if (supported) {
        try {
            zones = supported('timeZone');
        } catch {
            // keep the fallback
        }
    }
    // UTC is not always in the supported list; it is the backend default.
    return zones.includes('UTC') ? zones : ['UTC', ...zones];
}
