import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldRow, RowGroup, SettingsCard, checkboxClass, inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import type { ControlInput, EvidenceSource, EvidenceSourceDef, Member } from '@/api/compliance';
import { selectClass } from './selectClass';

const SEVERITIES = ['critical', 'high', 'medium', 'low'];

/** Create / edit a control. The server validates everything again. */
export function ControlForm({ title, initial, sources, members, busy, onSubmit, onCancel, submitLabel }: {
    title: string;
    initial: ControlInput;
    sources: EvidenceSourceDef[];
    members: Member[];
    busy: boolean;
    onSubmit: (value: ControlInput) => void;
    onCancel: () => void;
    submitLabel: string;
}) {
    const [value, setValue] = useState<ControlInput>(initial);
    const set = <K extends keyof ControlInput>(k: K, v: ControlInput[K]) => setValue((s) => ({ ...s, [k]: v }));
    const chosen = (type: string) => value.evidence_sources.find((s) => s.type === type);
    const toggleSource = (def: EvidenceSourceDef, on: boolean) =>
        set('evidence_sources', on
            ? [...value.evidence_sources, { type: def.type, params: { ...def.defaults } }]
            : value.evidence_sources.filter((s) => s.type !== def.type));
    const setParam = (type: string, key: string, v: unknown) =>
        set('evidence_sources', value.evidence_sources.map((s): EvidenceSource =>
            s.type === type ? { ...s, params: { ...s.params, [key]: v } } : s));
    const problem = !value.framework.trim() ? 'Name the framework'
        : !value.reference.trim() ? 'Give a reference, e.g. CC6.1'
            : !value.title.trim() ? 'Give the control a title'
                : !(value.freshness_days >= 1 && value.freshness_days <= 3650) ? 'Freshness must be 1–3650 days' : null;

    return (
        <SettingsCard
            labelledBy="control-form"
            title={title}
            description="Automated sources are evaluated live from TraceIQ's own data; you can add manual evidence once the control exists."
            footer={
                <>
                    <Button onClick={() => onSubmit(value)} disabled={busy || !!problem}>
                        {busy && <Loader2 className="h-4 w-4 animate-spin" />}{submitLabel}
                    </Button>
                    <Button variant="outline" onClick={onCancel} disabled={busy}>Cancel</Button>
                    {problem && <span className="text-xs text-ink-soft" role="status">{problem}</span>}
                </>
            }
        >
            <div className="flex flex-col divide-y divide-border">
                <FieldRow label="Framework" htmlFor="cf-framework" hint="e.g. SOC 2, ISO 27001, internal">
                    <Input id="cf-framework" className={inputClass} value={value.framework} maxLength={60} onChange={(e) => set('framework', e.target.value)} />
                </FieldRow>
                <FieldRow label="Reference" htmlFor="cf-reference" hint="The criterion or clause, e.g. CC6.1">
                    <Input id="cf-reference" className={inputClass} value={value.reference} maxLength={40} onChange={(e) => set('reference', e.target.value)} />
                </FieldRow>
                <FieldRow label="Title" htmlFor="cf-title" wide>
                    <Input id="cf-title" className={inputClass} value={value.title} maxLength={200} onChange={(e) => set('title', e.target.value)} />
                </FieldRow>
                <FieldRow label="Owner" htmlFor="cf-owner" hint="The owner can add evidence and request exceptions.">
                    <select id="cf-owner" className={`${selectClass} w-full`} value={value.owner_id ?? ''}
                        onChange={(e) => set('owner_id', e.target.value ? Number(e.target.value) : null)}>
                        <option value="">No owner</option>
                        {members.map((m) => <option key={m.id} value={m.id}>{m.full_name ? `${m.full_name} (${m.email})` : m.email}</option>)}
                    </select>
                </FieldRow>
                <FieldRow label="Freshness SLA (days)" htmlFor="cf-fresh" hint="Stale when the newest passing evidence is older than this.">
                    <Input id="cf-fresh" type="number" min={1} max={3650} className={inputClass} value={value.freshness_days}
                        onChange={(e) => set('freshness_days', Number(e.target.value))} />
                </FieldRow>
                <label className="flex flex-col gap-1.5 py-3 text-sm font-medium text-ink">
                    Description
                    <textarea className={`${textareaClass} font-sans`} rows={3} maxLength={4000} value={value.description ?? ''}
                        onChange={(e) => set('description', e.target.value)} placeholder="How the control is implemented" />
                </label>
            </div>
            <RowGroup title="Automated evidence sources">
                <ul className="flex flex-col divide-y divide-border">
                    {sources.map((def) => {
                        const src = chosen(def.type);
                        const id = `cf-src-${def.type}`;
                        return (
                            <li key={def.type} className="flex flex-col gap-2 py-2">
                                <label htmlFor={id} className="flex cursor-pointer items-start gap-3 text-sm">
                                    <input id={id} type="checkbox" className={`${checkboxClass} mt-0.5`} checked={!!src}
                                        onChange={(e) => toggleSource(def, e.target.checked)} />
                                    <span className="min-w-0">
                                        <span className="block font-medium text-ink">{def.label}</span>
                                        <span className="block text-xs text-ink-soft">{def.description}</span>
                                    </span>
                                </label>
                                {src && 'days' in def.defaults && (
                                    <label className="ml-8 flex items-center gap-2 text-xs text-ink">
                                        Within days
                                        <Input type="number" min={1} max={3650} className="h-8 w-24 rounded-full" value={Number(src.params.days ?? 90)}
                                            onChange={(e) => setParam(def.type, 'days', Number(e.target.value))} />
                                    </label>
                                )}
                                {src && 'max_days' in def.defaults && (
                                    <label className="ml-8 flex items-center gap-2 text-xs text-ink">
                                        Longest allowed key life (days)
                                        <Input type="number" min={1} max={3650} className="h-8 w-24 rounded-full" value={Number(src.params.max_days ?? 365)}
                                            onChange={(e) => setParam(def.type, 'max_days', Number(e.target.value))} />
                                    </label>
                                )}
                                {src && 'severities' in def.defaults && (
                                    <fieldset className="ml-8 flex flex-wrap items-center gap-3 text-xs text-ink">
                                        <legend className="sr-only">Severities</legend>
                                        Severities
                                        {SEVERITIES.map((sev) => {
                                            const list = (src.params.severities as string[] | undefined) ?? [];
                                            return (
                                                <label key={sev} className="flex items-center gap-1.5">
                                                    <input type="checkbox" className={checkboxClass} checked={list.includes(sev)}
                                                        onChange={(e) => setParam(def.type, 'severities',
                                                            e.target.checked ? [...list, sev] : list.filter((s) => s !== sev))} />
                                                    {sev}
                                                </label>
                                            );
                                        })}
                                    </fieldset>
                                )}
                            </li>
                        );
                    })}
                </ul>
            </RowGroup>
        </SettingsCard>
    );
}
