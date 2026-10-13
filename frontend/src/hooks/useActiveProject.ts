import { useSyncExternalStore } from 'react';

// The sidebar's project switcher writes `activeProjectId` to localStorage and
// fires a `projectChanged` window event. This subscribes to both without an
// effect, so a page re-renders the moment the project changes.
function subscribe(onChange: () => void) {
    window.addEventListener('projectChanged', onChange);
    window.addEventListener('storage', onChange);
    return () => {
        window.removeEventListener('projectChanged', onChange);
        window.removeEventListener('storage', onChange);
    };
}

function read(): number | null {
    const s = localStorage.getItem('activeProjectId');
    const n = s ? parseInt(s, 10) : NaN;
    return Number.isFinite(n) ? n : null;
}

export function useActiveProjectId(): number | null {
    return useSyncExternalStore(subscribe, read, () => null);
}
