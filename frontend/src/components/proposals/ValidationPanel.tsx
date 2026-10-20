import { Link } from 'react-router-dom';
import { FlaskConical } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Eyebrow, Pill } from '@/components/redesign/primitives';
import { conditionsText, validationLabel, type ValidationFields } from './repairValidation';

/**
 * Validation run for a repair proposal (WP16c): the change applied to ONE run
 * of the case only. Shows the state, links the run, and offers Validate.
 */
export default function ValidationPanel({ proposal, canValidate, onValidate, busy, block }: {
    proposal: ValidationFields;
    canValidate: boolean;
    onValidate: () => void;
    busy: boolean;
    /** Why Accept is disabled right now (acceptBlock), if it is. */
    block: string | null;
}) {
    const state = validationLabel(proposal.validation_status ?? null);
    const running = proposal.validation_status === 'validating';
    const conditions = conditionsText(proposal.validation_conditions);
    if (!canValidate && !proposal.validation_status) return null;
    return (
        <section aria-label="Validation run" className="flex flex-col gap-2">
            <Eyebrow right={proposal.validation_required ? <Pill tone="butter">Required before accept</Pill> : undefined}>
                Validation run
            </Eyebrow>
            <div className="flex flex-col gap-3 rounded-2xl border border-border bg-white px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={state.tone}>{state.label}</Pill>
                    {proposal.validation_run_id != null && (
                        <Link to={`/runs/${proposal.validation_run_id}`} className="text-sm font-semibold text-ink underline underline-offset-2">
                            Run #{proposal.validation_run_id}
                        </Link>
                    )}
                    {conditions && <span className="text-xs text-ink-soft">{conditions}</span>}
                </div>
                {proposal.validation_detail && <p className="text-xs text-ink-soft">{proposal.validation_detail}</p>}
                <p className="text-xs text-ink-soft">
                    Runs this case once with the change applied to that run only, under the failing run&apos;s
                    browser, device and environment. The stored test is not changed. Validation runs never
                    count towards pass rates, gates or flake scores.
                </p>
                {block && (
                    <p role="status" className="rounded-xl bg-pastel-butter/50 px-3 py-2 text-xs text-ink">
                        Accept is disabled: {block}
                    </p>
                )}
                {canValidate && proposal.status === 'pending' && (
                    <div>
                        <Button size="sm" variant="outline" className="h-10 px-4" disabled={busy || running} onClick={onValidate}>
                            <FlaskConical className="h-4 w-4" aria-hidden="true" />
                            {running ? 'Validation running…' : proposal.validation_status ? 'Validate again' : 'Validate'}
                        </Button>
                    </div>
                )}
            </div>
        </section>
    );
}
