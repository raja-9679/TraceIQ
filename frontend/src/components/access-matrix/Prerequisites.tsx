import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import type { MatrixRow } from '@/api/accessMatrix';
import { securityApi } from '@/api/security';
import { prerequisites } from './grid';

interface Props {
    projectId: number;
    rows: MatrixRow[];
    baseUrl: string | null | undefined;
    columns: string[];
}

/** The project's security-testing gate, shown before the user hits Run.
 * Request/page rows are refused server-side unless all of this holds. */
export default function Prerequisites({ projectId, rows, baseUrl, columns }: Props) {
    const sec = useQuery({ queryKey: ['sec-settings', projectId], queryFn: () => securityApi.getSettings(projectId) });
    const checks = prerequisites(rows, baseUrl, sec.data, columns);
    const domains = sec.data?.allowed_domains ?? [];

    return (
        <section aria-labelledby="am-prereq" className="rounded-2xl bg-background p-4">
            <h3 id="am-prereq" className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Security-testing prerequisites</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-ink">
                Request and page rows hit the target directly, so they only run when security testing is enabled for the
                project, every host is on its authorized domain list, and — for anything other than GET/HEAD/OPTIONS,
                including an ID-swap owner request — you attest you are authorized when you run. Test-case rows run as
                ordinary test runs and need none of this.
            </p>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <dt className="text-ink-soft">Security testing</dt>
                <dd className={sec.data?.enabled ? 'font-semibold text-pastel-ink-mint' : 'font-semibold text-pastel-ink-peach'}>
                    {sec.isLoading ? 'loading…' : sec.error ? 'could not load settings' : sec.data?.enabled ? 'enabled' : 'disabled'}
                </dd>
                <dt className="text-ink-soft">Authorized domains</dt>
                <dd className="break-words font-mono text-ink">{domains.length ? domains.join(', ') : 'none'}</dd>
            </dl>
            {checks.length > 0 && (
                <ul className="mt-3 space-y-1 text-xs">
                    {checks.map((c, i) => (
                        <li key={i} className={c.ok ? 'text-pastel-ink-mint' : 'text-pastel-ink-butter'}>
                            {c.ok ? 'OK: ' : 'Needs attention: '}{c.text}
                        </li>
                    ))}
                </ul>
            )}
            <Link to="/security" className="mt-3 inline-block text-xs font-semibold text-ink underline underline-offset-2">
                Edit security settings
            </Link>
        </section>
    );
}
