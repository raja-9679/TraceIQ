import type { ReactNode } from 'react';
import type { PublicPocReport } from '@/api/pocReport';
import { Tile } from '@/components/redesign-local/groupB/bits';
import { fmtPct } from '@/components/reports/reportHelpers';
import { executorLabel, fmtCount, fmtNames } from './pocHelpers';

// The PoC report body: used by the signed-in page and by the public share
// view, so it renders only fields the public payload also carries.

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
    return (
        <section aria-labelledby={id} className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6 print:break-inside-avoid">
            <h2 id={id} className="text-base font-semibold text-ink">{title}</h2>
            {children}
        </section>
    );
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
    return (
        <dl className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
            {rows.map(([k, v]) => (
                <div key={k} className="flex min-h-10 items-baseline justify-between gap-4 border-t border-[#F0EEE8] py-2 text-sm">
                    <dt className="text-ink-soft">{k}</dt>
                    <dd className="text-right font-medium text-ink">{v}</dd>
                </div>
            ))}
        </dl>
    );
}

export function PocReportView({ data }: { data: PublicPocReport }) {
    const { inventory: inv, activity: act, value: val, estimate: est } = data;
    const integ = inv.integrations;
    const sev = val.security_findings;
    return (
        <div className="flex flex-col gap-5">
            <section aria-label="Estimated time saved" className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6 shadow-[inset_4px_0_0_#8FD3B6]">
                <span className="text-[13px] font-semibold uppercase tracking-wide text-ink-soft">Estimate</span>
                <p className="text-[22px] font-bold leading-snug text-ink">{est.label}</p>
                {est.money && <p className="text-base font-semibold text-ink">{est.money.label}</p>}
                <p className="text-xs text-ink-soft">{est.basis} Change the assumptions to match how your team tests by hand.</p>
            </section>

            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Tile label="Runs" value={fmtCount(act.runs)} hint={`${data.days} day${data.days === 1 ? '' : 's'}`} />
                <Tile label="Pass rate" value={fmtPct(act.pass_rate)} />
                <Tile label="Automated case executions" value={fmtCount(act.case_executions)} />
                <Tile label="Failures caught" value={fmtCount(val.failures_caught)} />
            </div>

            <Section id="poc-setup" title="Setup">
                <Facts rows={[
                    ['Projects', fmtNames(inv.projects)],
                    ['Suites', fmtCount(inv.suites)],
                    ['Test cases', fmtCount(inv.cases)],
                    ['By type', Object.keys(inv.cases_by_executor).length
                        ? Object.entries(inv.cases_by_executor).map(([k, n]) => `${executorLabel(k)} ${fmtCount(n)}`).join(' · ')
                        : 'None'],
                    ['Environments', fmtNames(inv.environments)],
                    ['Monitors', fmtNames(inv.monitors)],
                    ['Issue trackers', fmtNames(integ.issue_trackers)],
                    ['Outbound webhooks', fmtCount(integ.outbound_webhooks)],
                    ['Incident webhooks', fmtCount(integ.incident_webhooks)],
                    ['Paging channels', fmtCount(integ.paging_channels)],
                    ['Status pages', fmtCount(integ.status_pages)],
                    ['API keys', fmtCount(integ.api_keys)],
                ]} />
            </Section>

            <Section id="poc-activity" title="Activity">
                <Facts rows={[
                    ['Finished runs', fmtCount(act.runs)],
                    ['Passed runs', fmtCount(act.passed_runs)],
                    ['Automated case executions', fmtCount(act.case_executions)],
                    ['Manual case executions', fmtCount(act.manual_case_executions)],
                    ['Browsers covered', fmtNames(act.browsers)],
                    ['Devices covered', fmtNames(act.devices)],
                ]} />
            </Section>

            <Section id="poc-value" title="What it found">
                <Facts rows={[
                    ['Failing test results', fmtCount(val.failures_caught)],
                    ['Distinct failure causes (clusters)', fmtCount(val.failure_clusters)],
                    ['Security findings', `${fmtCount(val.security_findings_total)}${val.security_findings_total
                        ? ` (critical ${fmtCount(sev.critical)}, high ${fmtCount(sev.high)}, medium ${fmtCount(sev.medium)}, low ${fmtCount(sev.low)}, info ${fmtCount(sev.info)})`
                        : ''}`],
                    ['Flaky tests identified', fmtCount(val.flaky_tests)],
                    ['Production incidents', fmtCount(val.incidents)],
                    ['Incidents linked to a test or failure', fmtCount(val.incidents_linked)],
                ]} />
            </Section>
        </div>
    );
}
