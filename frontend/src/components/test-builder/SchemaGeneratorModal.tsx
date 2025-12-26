import React, { useState } from 'react';
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Wand2, ArrowRight, Check, X } from "lucide-react";

interface SchemaField {
    id: string;
    key: string;
    path: string;
    type: 'string' | 'number' | 'boolean' | 'object' | 'array' | 'null';
    required: boolean;
    children?: SchemaField[];
    parentType?: 'object' | 'array';
}

interface SchemaGeneratorModalProps {
    onGenerate: (schema: string) => void;
}

export const SchemaGeneratorModal: React.FC<SchemaGeneratorModalProps> = ({ onGenerate }) => {
    const [open, setOpen] = useState(false);
    const [step, setStep] = useState<'input' | 'mapping'>('input');
    const [jsonInput, setJsonInput] = useState('');
    const [mapping, setMapping] = useState<SchemaField[]>([]);
    const [error, setError] = useState<string | null>(null);

    const parseJson = () => {
        try {
            const parsed = JSON.parse(jsonInput);
            const fields = generateFields(parsed, 'root');
            setMapping(fields);
            setStep('mapping');
            setError(null);
        } catch (e) {
            setError("Invalid JSON. Please check your input.");
        }
    };

    const generateFields = (obj: any, path: string, key: string = 'root', parentType?: 'object' | 'array'): SchemaField[] => {
        const id = crypto.randomUUID();
        let type: SchemaField['type'] = 'string';
        if (obj === null) type = 'null';
        else if (Array.isArray(obj)) type = 'array';
        else if (typeof obj === 'object') type = 'object';
        else if (typeof obj === 'number') type = 'number';
        else if (typeof obj === 'boolean') type = 'boolean';

        const field: SchemaField = {
            id,
            key,
            path,
            type,
            required: true,
            parentType
        };

        if (type === 'object') {
            const children: SchemaField[] = [];
            for (const k in obj) {
                children.push(...generateFields(obj[k], `${path}.${k}`, k, 'object'));
            }
            field.children = children;
        } else if (type === 'array' && obj.length > 0) {
            const children = generateFields(obj[0], `${path}[]`, 'items', 'array');
            field.children = children;
        }

        return [field];
    };

    const updateField = (id: string, updates: Partial<SchemaField>) => {
        const updateRecursive = (fields: SchemaField[]): SchemaField[] => {
            return fields.map(f => {
                if (f.id === id) {
                    return { ...f, ...updates };
                }
                if (f.children) {
                    return { ...f, children: updateRecursive(f.children) };
                }
                return f;
            });
        };
        setMapping(updateRecursive(mapping));
    };

    const buildSchema = () => {
        const constructSchema = (fields: SchemaField[]): any => {
            if (fields.length === 0) return {};

            const field = fields[0];
            const schema: any = { type: field.type };

            if (field.type === 'object' && field.children) {
                schema.properties = {};
                schema.required = [];
                field.children.forEach(child => {
                    schema.properties[child.key] = constructSchema([child]);
                    if (child.required) {
                        schema.required.push(child.key);
                    }
                });
                if (schema.required.length === 0) delete schema.required;
            } else if (field.type === 'array' && field.children) {
                schema.items = constructSchema(field.children);
            }

            return schema;
        };

        const rootField = mapping[0];
        let finalSchema = {};

        if (rootField) {
            if (rootField.type === 'object' && rootField.children) {
                finalSchema = { type: 'object', properties: {}, required: [] as string[] };
                rootField.children.forEach(child => {
                    (finalSchema as any).properties[child.key] = constructSchema([child]);
                    if (child.required) {
                        (finalSchema as any).required.push(child.key);
                    }
                });
                if ((finalSchema as any).required.length === 0) delete (finalSchema as any).required;
            } else if (rootField.type === 'array' && rootField.children) {
                finalSchema = { type: 'array', items: constructSchema(rootField.children) };
            } else {
                finalSchema = { type: rootField.type };
            }
        }

        onGenerate(JSON.stringify(finalSchema, null, 2));
        setOpen(false);
        setStep('input');
        setJsonInput('');
    };

    const renderFieldRow = (field: SchemaField, depth: number = 0) => {
        return (
            <div key={field.id} className="flex flex-col">
                <div className="grid grid-cols-[40px_1fr_120px] gap-2 items-center py-1 hover:bg-slate-50 rounded px-2">
                    <div className="flex justify-center">
                        {field.parentType === 'object' && (
                            <input
                                type="checkbox"
                                checked={field.required}
                                onChange={(e) => updateField(field.id, { required: e.target.checked })}
                                className="h-4 w-4 accent-slate-900 cursor-pointer"
                                title="Mark as Required"
                            />
                        )}
                    </div>
                    <div className="text-sm font-mono text-slate-700 truncate" style={{ paddingLeft: `${depth * 20}px` }}>
                        {field.key}
                    </div>
                    <div>
                        <Select
                            value={field.type}
                            onValueChange={(v: any) => updateField(field.id, { type: v })}
                        >
                            <SelectTrigger className="h-6 w-full text-xs">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="string">String</SelectItem>
                                <SelectItem value="number">Number</SelectItem>
                                <SelectItem value="boolean">Boolean</SelectItem>
                                <SelectItem value="object">Object</SelectItem>
                                <SelectItem value="array">Array</SelectItem>
                                <SelectItem value="null">Null</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                </div>
                {field.children && field.children.map(child => renderFieldRow(child, depth + 1))}
            </div>
        );
    };

    return (
        <>
            <Button variant="outline" size="sm" className="text-xs h-6" onClick={() => setOpen(true)}>
                <Wand2 className="w-3 h-3 mr-1" /> Generate from Sample
            </Button>

            {open && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                    <div className="bg-white rounded-lg shadow-lg w-full max-w-2xl max-h-[80vh] flex flex-col m-4">
                        <div className="flex items-center justify-between p-4 border-b">
                            <h2 className="text-lg font-semibold">Generate JSON Schema</h2>
                            <Button variant="ghost" size="icon" onClick={() => setOpen(false)}>
                                <X className="w-4 h-4" />
                            </Button>
                        </div>

                        <div className="flex-1 overflow-hidden flex flex-col min-h-[300px] p-4">
                            {step === 'input' ? (
                                <div className="flex-1 flex flex-col gap-2">
                                    <p className="text-sm text-slate-500">Paste your JSON response sample below.</p>
                                    <textarea
                                        className="flex-1 font-mono text-xs p-4 border rounded-md resize-none focus:outline-none focus:ring-2 focus:ring-slate-400"
                                        placeholder='{"id": 1, "name": "Example"}'
                                        value={jsonInput}
                                        onChange={e => setJsonInput(e.target.value)}
                                    />
                                    {error && <p className="text-xs text-red-500">{error}</p>}
                                </div>
                            ) : (
                                <div className="flex-1 flex flex-col gap-2 overflow-hidden">
                                    <p className="text-sm text-slate-500">Review detected fields. Uncheck boxes to make fields optional.</p>
                                    <div className="border rounded-md flex-1 overflow-hidden flex flex-col">
                                        <div className="bg-slate-100 p-2 border-b grid grid-cols-[40px_1fr_120px] gap-2 text-xs font-medium text-slate-500 items-center">
                                            <span className="text-center">Req</span>
                                            <span>Field Name</span>
                                            <span>Type</span>
                                        </div>
                                        <div className="flex-1 overflow-y-auto">
                                            <div className="py-2">
                                                {mapping.length > 0 && mapping[0].children ? (
                                                    mapping[0].children.map(child => renderFieldRow(child, 0))
                                                ) : (
                                                    <div className="p-4 text-center text-sm text-slate-400">
                                                        Root is a primitive or empty.
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="p-4 border-t flex justify-end gap-2">
                            {step === 'input' ? (
                                <Button onClick={parseJson} disabled={!jsonInput.trim()}>
                                    Next <ArrowRight className="w-4 h-4 ml-2" />
                                </Button>
                            ) : (
                                <>
                                    <Button variant="ghost" onClick={() => setStep('input')}>Back</Button>
                                    <Button onClick={buildSchema}>
                                        <Check className="w-4 h-4 mr-2" /> Insert Schema
                                    </Button>
                                </>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};
