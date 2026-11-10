import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { estatesApi, type Estate, type EstateInput } from '@/api/estates';
import { Button } from '@/components/ui/button';
import { errDetail } from '@/lib/errors';
import { splitList } from './helpers';

interface Props {
    projectId: number;
    estate: Estate | null;
    onSaved: (e: Estate) => void;
    onCancel?: () => void;
}

export default function EstateForm({ projectId, estate, onSaved, onCancel }: Props) {
    const qc = useQueryClient();
    const [name, setName] = useState(estate?.name ?? '');
    const [cron, setCron] = useState(estate?.cron_expression ?? '0 3 * * *');
    const [scheduled, setScheduled] = useState(estate?.schedule_enabled ?? true);
    const [discover, setDiscover] = useState(estate?.discover_subdomains ?? true);
    const [selectors, setSelectors] = useState((estate?.dkim_selectors ?? []).join(', '));
    const [alertDays, setAlertDays] = useState((estate?.cert_alert_days ?? [30, 14, 7]).join(', '));
    const [emails, setEmails] = useState((estate?.alert_emails ?? []).join(', '));

    const save = useMutation({
        mutationFn: () => {
            const body: EstateInput = {
                name, cron_expression: cron, schedule_enabled: scheduled, discover_subdomains: discover,
                dkim_selectors: splitList(selectors),
                cert_alert_days: splitList(alertDays).map(Number).filter(n => Number.isFinite(n)),
                alert_emails: splitList(emails),
            };
            return estate ? estatesApi.update(estate.id, body) : estatesApi.create(projectId, body);
        },
        onSuccess: (e) => {
            qc.invalidateQueries({ queryKey: ['estates', projectId] });
            qc.invalidateQueries({ queryKey: ['estate', e.id] });
            onSaved(e);
        },
    });

    const field = 'mt-1 h-10 w-full rounded-xl border border-border bg-white px-3 text-sm text-ink';
    return (
        <form className="flex flex-col gap-4"
            onSubmit={(ev) => { ev.preventDefault(); save.mutate(); }}>
            {estate ? <h3 className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Estate settings</h3> : <h3 className="sr-only">New estate</h3>}
            <label className="block text-xs font-semibold text-ink-soft">Name
                <input className={field} value={name} onChange={e => setName(e.target.value)} required maxLength={200} />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-xs font-semibold text-ink-soft">Scan schedule (cron, UTC, at most hourly)
                    <input className={field} value={cron} onChange={e => setCron(e.target.value)} />
                </label>
                <label className="block text-xs font-semibold text-ink-soft">Certificate alerts (days before expiry)
                    <input className={field} value={alertDays} onChange={e => setAlertDays(e.target.value)} />
                </label>
                <label className="block text-xs font-semibold text-ink-soft">Extra DKIM selectors
                    <input className={field} value={selectors} onChange={e => setSelectors(e.target.value)}
                        placeholder="brand2024, mailer" />
                </label>
                <label className="block text-xs font-semibold text-ink-soft">Alert emails
                    <input className={field} value={emails} onChange={e => setEmails(e.target.value)}
                        placeholder="secops@example.com" />
                </label>
            </div>
            <div className="flex flex-wrap gap-4 text-sm text-ink">
                <label className="flex items-center gap-2">
                    <input type="checkbox" checked={scheduled} onChange={e => setScheduled(e.target.checked)} />
                    Scheduled scans
                </label>
                <label className="flex items-center gap-2">
                    <input type="checkbox" checked={discover} onChange={e => setDiscover(e.target.checked)} />
                    Discover subdomains from certificate transparency
                </label>
            </div>
            {save.error && <p role="alert" className="text-sm text-pastel-ink-peach">{errDetail(save.error)}</p>}
            <div className="flex gap-2">
                <Button type="submit" disabled={save.isPending || !name.trim()}>{save.isPending ? 'Saving…' : 'Save estate'}</Button>
                {onCancel && <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>}
            </div>
        </form>
    );
}
