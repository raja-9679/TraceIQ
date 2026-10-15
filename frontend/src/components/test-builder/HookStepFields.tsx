import React from 'react';
import { Input } from '@/components/ui/input';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { EMAIL_EXTRACT_OPTIONS, emailVariable, str, webhookVariable, whereError, whereToText, type HookParams } from './hookSteps';

interface FieldsProps {
    params: HookParams;
    onParam: (key: string, value: unknown) => void;
    hue: string;
}

const field = (hue: string, extra = '') =>
    `min-w-0 h-11 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${hue}-500/20 bg-white px-4 font-mono ${extra}`;

export const EmailWaitFields: React.FC<FieldsProps> = ({ params, onParam, hue }) => {
    const mode = str(params?.extract) || 'link';
    const variable = emailVariable(params);
    return (
        <div className="flex-1 w-full space-y-2">
            <div className="grid gap-2 sm:grid-cols-2">
                <Input aria-label="Inbox address" placeholder="To (default {{inbox.address}})"
                    value={str(params?.to)} onChange={(e) => onParam('to', e.target.value)} className={field(hue)} />
                <Input aria-label="Subject contains" placeholder="Subject contains (optional)"
                    value={str(params?.subject)} onChange={(e) => onParam('subject', e.target.value)} className={field(hue)} />
                <Input aria-label="From contains" placeholder="From contains (optional)"
                    value={str(params?.from)} onChange={(e) => onParam('from', e.target.value)} className={field(hue)} />
                <Input aria-label="Timeout (ms)" placeholder="Timeout ms (default 60000)" inputMode="numeric"
                    value={str(params?.timeout)} onChange={(e) => onParam('timeout', e.target.value)} className={field(hue)} />
            </div>
            <div className="flex gap-2 flex-col sm:flex-row">
                <Select value={mode} onValueChange={(value) => onParam('extract', value)}>
                    <SelectTrigger aria-label="Extract" className="sm:w-48 h-11 rounded-xl border-none shadow-sm bg-white">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {EMAIL_EXTRACT_OPTIONS.map((o) => (
                            <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                {(mode === 'link' || mode === 'code') && (
                    <Input aria-label="Pattern"
                        placeholder={mode === 'link' ? 'Link must match (regex, e.g. /reset)' : 'Code regex (default \\b(\\d{6})\\b)'}
                        value={str(params?.pattern)} onChange={(e) => onParam('pattern', e.target.value)}
                        className={field(hue, 'flex-[2]')} />
                )}
                {mode !== 'none' && (
                    <Input aria-label="Save value as" placeholder={`Save as (default ${variable})`}
                        value={str(params?.variableName)} onChange={(e) => onParam('variableName', e.target.value)}
                        className={field(hue, 'flex-1')} />
                )}
            </div>
            <p className="text-xs text-slate-500">
                Type {'{{inbox.address}}'} into the app's email field first — each run gets its own address on the
                instance's test inbox.{variable ? ` Then use {{${variable}}} in a later step.` : ''}
            </p>
        </div>
    );
};

export const WebhookWaitFields: React.FC<FieldsProps> = ({ params, onParam, hue }) => {
    const [whereText, setWhereText] = React.useState(() => whereToText(params?.where));
    const error = whereError(whereText);
    const variable = webhookVariable(params);
    return (
        <div className="flex-1 w-full space-y-2">
            <div className="grid gap-2 sm:grid-cols-2">
                <Input aria-label="Capture URL" placeholder="URL (default {{webhook.url}})"
                    value={str(params?.url)} onChange={(e) => onParam('url', e.target.value)} className={field(hue)} />
                <Input aria-label="Method" placeholder="Method (any)"
                    value={str(params?.method)} onChange={(e) => onParam('method', e.target.value.toUpperCase())} className={field(hue)} />
                <Input aria-label="Path contains" placeholder="Path after the token contains (optional)"
                    value={str(params?.path)} onChange={(e) => onParam('path', e.target.value)} className={field(hue)} />
                <Input aria-label="Timeout (ms)" placeholder="Timeout ms (default 60000)" inputMode="numeric"
                    value={str(params?.timeout)} onChange={(e) => onParam('timeout', e.target.value)} className={field(hue)} />
            </div>
            <Input aria-label="Body must match (JSON)" placeholder='Body must match, e.g. {"type": "order.paid"}'
                value={whereText}
                onChange={(e) => {
                    setWhereText(e.target.value);
                    // Stored as typed; the worker parses it. Only valid JSON is
                    // saved so a half-typed object never reaches a run.
                    if (!whereError(e.target.value)) onParam('where', e.target.value.trim() || undefined);
                }}
                className={field(hue, 'w-full')} />
            {error && <p className="text-xs text-rose-600">{error}</p>}
            <div className="grid gap-2 sm:grid-cols-3">
                <Input aria-label="Extract JSON path" placeholder="Extract JSON path (e.g. data.id)"
                    value={str(params?.extract)} onChange={(e) => onParam('extract', e.target.value)} className={field(hue)} />
                <Input aria-label="Must equal" placeholder="Must equal (optional)"
                    value={str(params?.equals)} onChange={(e) => onParam('equals', e.target.value)} className={field(hue)} />
                <Input aria-label="Save value as" placeholder={`Save as (default ${variable})`}
                    value={str(params?.variableName)} onChange={(e) => onParam('variableName', e.target.value)} className={field(hue)} />
            </div>
            <p className="text-xs text-slate-500">
                Give the app {'{{webhook.url}}'} as its callback URL — it must be able to reach this TraceIQ instance.
                Credential headers and fields like token/password are redacted before storage. Use {`{{${variable}}}`} later.
            </p>
        </div>
    );
};
