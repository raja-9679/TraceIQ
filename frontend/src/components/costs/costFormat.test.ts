import { describe, expect, it } from 'vitest';
import { NOT_PRICED, budgetTone, formatMoney, parseBudget } from './costFormat';
import { rowsFromTable, tableFromRows } from '../settings/costPrices';

describe('cost formatting', () => {
    it('shows not priced instead of zero', () => {
        expect(formatMoney(null, 'EUR')).toBe(NOT_PRICED);
        expect(formatMoney(3, null)).toBe(NOT_PRICED);
        expect(formatMoney(0, 'EUR')).not.toBe(NOT_PRICED);
        expect(formatMoney(1.5, 'EUR')).toContain('1.50');
    });

    it('budget tones follow the alert thresholds', () => {
        expect(budgetTone(null)).toBe('neutral');
        expect(budgetTone(79.9)).toBe('mint');
        expect(budgetTone(80)).toBe('butter');
        expect(budgetTone(100)).toBe('peach');
    });

    it('parses budgets', () => {
        expect(parseBudget('')).toEqual({ value: null });
        expect(parseBudget('12.345')).toEqual({ value: 12.35 });
        expect(parseBudget('-1')).toHaveProperty('error');
        expect(parseBudget('lots')).toHaveProperty('error');
    });
});

describe('LLM price table rows', () => {
    it('round-trips a table', () => {
        const table = { 'model-a': { input_per_1m: 3, output_per_1m: 15 } };
        expect(tableFromRows(rowsFromTable(table))).toEqual({ table });
        expect(rowsFromTable(null)).toEqual([]);
    });

    it('drops empty rows and refuses half-filled or duplicate ones', () => {
        expect(tableFromRows([{ model: '', input: '', output: '' }])).toEqual({ table: {} });
        expect(tableFromRows([{ model: 'm', input: '1', output: '' }])).toHaveProperty('error');
        expect(tableFromRows([{ model: '', input: '1', output: '2' }])).toHaveProperty('error');
        expect(tableFromRows([{ model: 'm', input: '-1', output: '2' }])).toHaveProperty('error');
        expect(tableFromRows([
            { model: 'M', input: '1', output: '2' }, { model: 'm', input: '1', output: '2' },
        ])).toHaveProperty('error');
    });
});
