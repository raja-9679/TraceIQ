import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { justificationProblem } from '@/components/estate/helpers';

interface Props {
    scriptLabel: string;
    pending: boolean;
    onSubmit: (justification: string) => void;
    onCancel: () => void;
}

/** PCI DSS 6.4.3: an approval needs a written justification. */
export default function ApproveForm({ scriptLabel, pending, onSubmit, onCancel }: Props) {
    const [text, setText] = useState('');
    const [touched, setTouched] = useState(false);
    const problem = justificationProblem(text);
    const id = `justify-${scriptLabel.replace(/[^a-z0-9]/gi, '').slice(0, 40)}`;
    return (
        <form className="mt-2 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3"
            onSubmit={(e) => { e.preventDefault(); setTouched(true); if (!problem) onSubmit(text.trim()); }}>
            <label htmlFor={id} className="block text-xs font-medium text-slate-700">
                Why is this script needed on the page? (business or technical justification)
            </label>
            <textarea id={id} rows={2} value={text} onChange={e => setText(e.target.value)}
                className="w-full rounded border border-slate-200 p-2 text-sm" aria-invalid={touched && !!problem}
                aria-describedby={`${id}-err`} />
            {touched && problem && <p id={`${id}-err`} role="alert" className="text-xs text-rose-700">{problem}</p>}
            <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={pending}>{pending ? 'Approving…' : 'Approve script'}</Button>
                <Button type="button" size="sm" variant="outline" onClick={onCancel}>Cancel</Button>
            </div>
        </form>
    );
}
