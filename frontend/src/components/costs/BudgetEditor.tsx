import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { errorDetail } from '@/api/idpCatalog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldRow, SettingsCard, inputClass } from '@/components/redesign-local/groupD/settingsForm';
import { costsApi } from '@/api/costs';
import { parseBudget } from './costFormat';

/** Monthly budget for one project. Project admins edit; alerts at 80% and 100%
 * go to the project's own notification recipients and webhooks. */
export default function BudgetEditor({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
    const queryClient = useQueryClient();
    const budget = useQuery({ queryKey: ['cost-budget', projectId], queryFn: () => costsApi.budget(projectId) });
    // null = not edited: show the saved amount.
    const [draft, setDraft] = useState<string | null>(null);
    const saved = budget.data?.monthly_amount;
    const raw = draft ?? (saved != null ? String(saved) : '');
    const setRaw = (v: string) => setDraft(v);
    const parsed = parseBudget(raw);
    const save = useMutation({
        mutationFn: () => costsApi.setBudget(projectId, 'value' in parsed ? parsed.value : null),
        onSuccess: () => {
            toast.success('Budget saved');
            setDraft(null);
            queryClient.invalidateQueries({ queryKey: ['cost-budget', projectId] });
            queryClient.invalidateQueries({ queryKey: ['cost-report'] });
        },
        onError: (err) => toast.error(errorDetail(err, 'Could not save the budget')),
    });
    const currency = budget.data?.currency;

    return (
        <SettingsCard
            labelledBy="cost-budget"
            title="Monthly budget"
            description={currency
                ? 'Alerts at 80% and 100% of this month\'s spend, once each per month, through this project\'s notification settings.'
                : 'Set a currency and prices in Instance settings → Cost first; budgets are checked against priced spend.'}
            footer={canEdit ? (
                <>
                    <Button onClick={() => save.mutate()} disabled={'error' in parsed || save.isPending}>
                        {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save budget
                    </Button>
                    {'error' in parsed && <span className="text-xs text-ink-soft" role="status">{parsed.error}</span>}
                </>
            ) : <span className="text-xs text-ink-soft">Changing the budget needs the project admin role.</span>}
        >
            <FieldRow label={`Budget per month${currency ? ` (${currency})` : ''}`} htmlFor="cost-budget-amount" hint="Leave empty for no budget.">
                <Input id="cost-budget-amount" type="number" min={0} step="0.01" className={inputClass}
                    value={raw} disabled={!canEdit} onChange={(e) => setRaw(e.target.value)} />
            </FieldRow>
        </SettingsCard>
    );
}
