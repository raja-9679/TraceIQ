import React, { useState } from 'react';
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Wand2, ArrowRight, Check, X, ChevronRight, ChevronDown } from "lucide-react";

interface AssertionRequest {
    type: 'xpath';
    key: string; // xpath
    operator: 'equals' | 'exists' | 'contains' | 'optional' | 'matches';
    value?: string;
}

interface FeedAssertionGeneratorModalProps {
    onGenerate: (assertions: AssertionRequest[]) => void;
}

interface XmlNode {
    id: string;
    tagName: string;
    localName: string;
    namespaceURI: string | null;
    textContent: string | null;
    path: string;
    strictPath: string;
    children: XmlNode[];
    attributes: { name: string; value: string }[];
    count?: number;
}

interface ManualAssertion {
    id: string;
    path: string;
    operator: 'equals' | 'exists' | 'contains' | 'optional' | 'matches';
    value: string;
}

interface NodeSelection {
    selected: boolean;
    operator: 'exists' | 'equals' | 'contains' | 'optional' | 'matches';
}

export const FeedAssertionGeneratorModal: React.FC<FeedAssertionGeneratorModalProps> = ({ onGenerate }) => {
    const [open, setOpen] = useState(false);
    const [step, setStep] = useState<'input' | 'selection'>('input');
    const [xmlInput, setXmlInput] = useState('');
    const [rootNode, setRootNode] = useState<XmlNode | null>(null);
    const [selections, setSelections] = useState<Record<string, NodeSelection>>({});
    const [manualAssertions, setManualAssertions] = useState<ManualAssertion[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({});
    const [strictMode, setStrictMode] = useState(false);

    const parseXml = () => {
        try {
            const parser = new DOMParser();
            const doc = parser.parseFromString(xmlInput, "text/xml");
            const parseError = doc.querySelector("parsererror");
            if (parseError) {
                throw new Error(parseError.textContent || "XML Parse Error");
            }

            const root = buildNodeTree(doc.documentElement, "", "");
            setRootNode(root);
            setStep('selection');
            setError(null);
            // Expand root by default
            setExpandedNodes({ [root.id]: true });
        } catch (e: any) {
            setError("Invalid XML: " + e.message);
        }
    };

    const buildNodeTree = (element: Element, parentPath: string, parentStrictPath: string): XmlNode => {
        const id = crypto.randomUUID();
        const tagName = element.tagName;
        const localName = element.localName || tagName.split(':').pop() || tagName;
        const namespaceURI = element.namespaceURI;

        // Robust XPath (name-based)
        const xpathTag = `*[name()='${tagName}']`;

        // Strict XPath (local-name + namespace-uri)
        const strictTag = namespaceURI
            ? `*[local-name()='${localName}' and namespace-uri()='${namespaceURI}']`
            : `*[local-name()='${localName}' and not(namespace-uri())]`;

        // Calculate index among siblings with same tag name
        let index = 1;
        let sibling = element.previousElementSibling;
        while (sibling) {
            if (sibling.tagName === tagName) index++;
            sibling = sibling.previousElementSibling;
        }

        const currentPath = parentPath ? `${parentPath}/${xpathTag}[${index}]` : `/${xpathTag}`;
        const currentStrictPath = parentStrictPath ? `${parentStrictPath}/${strictTag}[${index}]` : `/${strictTag}`;

        const children: XmlNode[] = [];
        const seenTags = new Set<string>();

        for (let i = 0; i < element.children.length; i++) {
            const child = element.children[i];
            const childTagName = child.tagName;

            if (seenTags.has(childTagName)) {
                // Find existing child and increment count
                const existing = children.find(c => c.tagName === childTagName);
                if (existing) {
                    existing.count = (existing.count || 1) + 1;
                }
                continue;
            }

            children.push(buildNodeTree(child, currentPath, currentStrictPath));
            seenTags.add(childTagName);
        }

        const attributes = [];
        for (let i = 0; i < element.attributes.length; i++) {
            attributes.push({
                name: element.attributes[i].name,
                value: element.attributes[i].value
            });
        }

        let textContent = null;
        if (element.children.length === 0) {
            textContent = element.textContent;
        }

        return {
            id,
            tagName,
            localName,
            namespaceURI,
            textContent,
            path: currentPath,
            strictPath: currentStrictPath,
            children,
            attributes,
            count: 1
        };
    };

    const toggleSelection = (id: string, checked: boolean) => {
        setSelections(prev => ({
            ...prev,
            [id]: {
                selected: checked,
                operator: prev[id]?.operator || 'exists'
            }
        }));
    };

    const updateOperator = (id: string, operator: 'exists' | 'equals' | 'contains' | 'optional' | 'matches') => {
        setSelections(prev => ({
            ...prev,
            [id]: {
                selected: true,
                operator
            }
        }));
    };

    const addManualAssertion = () => {
        setManualAssertions(prev => [
            ...prev,
            { id: crypto.randomUUID(), path: '', operator: 'equals', value: '' }
        ]);
    };

    const updateManualAssertion = (id: string, field: keyof ManualAssertion, value: string) => {
        setManualAssertions(prev => prev.map(a => a.id === id ? { ...a, [field]: value } : a));
    };

    const removeManualAssertion = (id: string) => {
        setManualAssertions(prev => prev.filter(a => a.id !== id));
    };

    const toggleExpand = (id: string) => {
        setExpandedNodes(prev => ({ ...prev, [id]: !prev[id] }));
    };

    const generateAssertions = () => {
        const assertions: AssertionRequest[] = [];

        const traverse = (node: XmlNode) => {
            const selection = selections[node.id];
            if (selection && selection.selected) {
                assertions.push({
                    type: 'xpath',
                    key: strictMode ? node.strictPath : node.path,
                    operator: selection.operator,
                    value: (selection.operator !== 'exists' && selection.operator !== 'optional') ? (node.textContent || '') : undefined
                });
            }
            node.children.forEach(traverse);
        };

        if (rootNode) traverse(rootNode);

        // Add manual assertions
        manualAssertions.forEach(ma => {
            if (ma.path) {
                assertions.push({
                    type: 'xpath',
                    key: ma.path,
                    operator: ma.operator,
                    value: (ma.operator !== 'exists' && ma.operator !== 'optional') ? ma.value : undefined
                });
            }
        });

        onGenerate(assertions);
        setOpen(false);
        setStep('input');
        setXmlInput('');
        setSelections({});
        setManualAssertions([]);
    };

    const renderNode = (node: XmlNode, depth: number) => {
        const isExpanded = expandedNodes[node.id];
        const hasChildren = node.children.length > 0;
        const selection = selections[node.id] || { selected: false, operator: 'exists' };

        return (
            <div key={node.id} className="flex flex-col">
                <div className="flex items-center gap-2 py-1 hover:bg-slate-50 rounded px-2" style={{ paddingLeft: `${depth * 20}px` }}>
                    <button
                        onClick={() => toggleExpand(node.id)}
                        className={`p-0.5 rounded hover:bg-slate-200 ${!hasChildren ? 'invisible' : ''}`}
                    >
                        {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                    </button>

                    <input
                        type="checkbox"
                        checked={selection.selected}
                        onChange={(e) => toggleSelection(node.id, e.target.checked)}
                        className="h-4 w-4 accent-ink cursor-pointer"
                    />

                    <div className="flex flex-col min-w-0">
                        <span className="text-xs font-mono text-ink font-medium truncate">
                            &lt;{node.tagName}&gt;
                            {node.count && node.count > 1 && (
                                <span className="ml-1 text-[11px] text-ink-soft font-normal">(x{node.count})</span>
                            )}
                        </span>
                        {strictMode && node.namespaceURI && (
                            <span className="text-[11px] text-ink-soft font-mono truncate max-w-[200px]" title={node.namespaceURI}>
                                {node.namespaceURI}
                            </span>
                        )}
                    </div>

                    {node.textContent && (
                        <span className="text-xs text-ink-soft truncate max-w-[150px]" title={node.textContent}>
                            {node.textContent}
                        </span>
                    )}

                    {selection.selected && (
                        <div className="ml-auto flex items-center gap-2">
                            <Select
                                value={selection.operator}
                                onValueChange={(v: 'exists' | 'equals' | 'contains' | 'optional') => updateOperator(node.id, v)}
                            >
                                <SelectTrigger className="h-6 w-[100px] text-xs">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="exists">Exists</SelectItem>
                                    <SelectItem value="optional">Optional</SelectItem>
                                    <SelectItem value="equals">Equals</SelectItem>
                                    <SelectItem value="contains">Contains</SelectItem>
                                    <SelectItem value="matches">Matches (Regex)</SelectItem>
                                </SelectContent>
                            </Select>
                            {(selection.operator !== 'exists' && selection.operator !== 'optional') && (
                                <input
                                    type="text"
                                    className="h-6 w-[120px] text-xs border rounded px-2 py-1 bg-slate-50"
                                    placeholder="Expected Value"
                                    value={node.textContent || ''}
                                    readOnly
                                    disabled
                                />
                            )}
                        </div>
                    )}
                </div>

                {isExpanded && node.children.map(child => renderNode(child, depth + 1))}
            </div>
        );
    };

    return (
        <>
            <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
                <Wand2 className="w-3.5 h-3.5" /> Generate assertions
            </Button>

            {open && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 backdrop-blur-sm">
                    <div role="dialog" aria-modal="true" aria-labelledby="feed-gen-title" className="bg-white rounded-3xl border border-border shadow-[0_12px_40px_rgba(28,27,34,0.12)] w-full max-w-3xl max-h-[90vh] flex flex-col m-4">
                        <div className="flex items-center justify-between gap-3 p-5 border-b border-border">
                            <div className="flex flex-col">
                                <h2 id="feed-gen-title" className="text-[22px] font-bold tracking-[-0.02em] text-ink">Generate feed assertions</h2>
                                <p className="text-sm text-ink-soft">Pick nodes to turn into XPath assertions.</p>
                            </div>
                            <Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Close" className="text-ink-soft hover:text-ink">
                                <X className="w-4 h-4" />
                            </Button>
                        </div>

                        <div className="flex-1 overflow-hidden flex flex-col min-h-[300px] p-5">
                            {step === 'input' ? (
                                <div className="flex-1 flex flex-col gap-2">
                                    <p className="text-sm text-ink-soft">Paste your XML or RSS feed below.</p>
                                    <textarea
                                        className="flex-1 font-mono text-xs p-4 border border-border bg-background text-ink rounded-2xl resize-none focus:outline-none focus:ring-2 focus:ring-ring"
                                        placeholder='<?xml version="1.0" ...'
                                        value={xmlInput}
                                        onChange={(e) => setXmlInput(e.target.value)}
                                    />
                                    {error && <p role="alert" className="text-sm text-pastel-ink-peach">{error}</p>}
                                </div>
                            ) : (
                                <div className="flex-1 flex flex-col gap-4 overflow-hidden">
                                    <div className="flex-1 flex flex-col gap-2 overflow-hidden">
                                        <div className="flex items-center justify-between">
                                            <p className="text-sm font-semibold text-ink">Nodes in the sample</p>
                                            <label className="flex min-h-10 cursor-pointer items-center gap-2 rounded-full border border-border px-3.5 text-sm text-ink">
                                                <input
                                                    type="checkbox"
                                                    checked={strictMode}
                                                    onChange={e => setStrictMode(e.target.checked)}
                                                    className="h-4 w-4 accent-ink cursor-pointer"
                                                />
                                                Strict namespaces
                                            </label>
                                        </div>
                                        <div className="border border-border rounded-2xl flex-1 overflow-hidden flex flex-col">
                                            <div className="bg-background p-2 border-b border-border text-xs font-semibold text-ink-soft px-4 flex justify-between">
                                                <span>XML structure</span>
                                                <span>Assertion type</span>
                                            </div>
                                            <div className="flex-1 overflow-y-auto p-2">
                                                {rootNode && renderNode(rootNode, 0)}
                                            </div>
                                        </div>
                                    </div>

                                    <div className="flex flex-col gap-2">
                                        <div className="flex items-center justify-between">
                                            <p className="text-sm font-semibold text-ink">Manual assertions</p>
                                            <Button variant="outline" size="sm" onClick={addManualAssertion}>
                                                Add manual assertion
                                            </Button>
                                        </div>
                                        <div className="space-y-2 max-h-[150px] overflow-y-auto pr-2">
                                            {manualAssertions.map(ma => (
                                                <div key={ma.id} className="flex gap-2 items-center">
                                                    <input
                                                        className="flex-1 h-9 text-xs px-2.5 border border-border rounded-xl bg-white text-ink font-mono"
                                                        aria-label="XPath"
                                                        placeholder="XPath (e.g. //item/title)"
                                                        value={ma.path}
                                                        onChange={e => updateManualAssertion(ma.id, 'path', e.target.value)}
                                                    />
                                                    <Select
                                                        value={ma.operator}
                                                        onValueChange={(v: any) => updateManualAssertion(ma.id, 'operator', v)}
                                                    >
                                                        <SelectTrigger className="h-9 w-[110px] text-xs rounded-xl">
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            <SelectItem value="exists">Exists</SelectItem>
                                                            <SelectItem value="optional">Optional</SelectItem>
                                                            <SelectItem value="equals">Equals</SelectItem>
                                                            <SelectItem value="contains">Contains</SelectItem>
                                                            <SelectItem value="matches">Matches (Regex)</SelectItem>
                                                        </SelectContent>
                                                    </Select>
                                                    <input
                                                        className="flex-1 h-9 text-xs px-2.5 border border-border rounded-xl bg-white text-ink disabled:opacity-50"
                                                        aria-label="Expected value"
                                                        placeholder="Expected value"
                                                        value={ma.value}
                                                        onChange={e => updateManualAssertion(ma.id, 'value', e.target.value)}
                                                        disabled={ma.operator === 'exists' || ma.operator === 'optional'}
                                                    />
                                                    <Button variant="ghost" size="icon" aria-label="Remove manual assertion" className="h-9 w-9 text-ink-soft hover:text-ink" onClick={() => removeManualAssertion(ma.id)}>
                                                        <X className="w-4 h-4" />
                                                    </Button>
                                                </div>
                                            ))}
                                            {manualAssertions.length === 0 && (
                                                <p className="text-sm text-ink-soft text-center py-2">No manual assertions yet.</p>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="p-5 border-t border-border flex justify-end gap-2">
                            {step === 'input' ? (
                                <Button onClick={parseXml} disabled={!xmlInput.trim()}>
                                    Next <ArrowRight className="w-4 h-4" />
                                </Button>
                            ) : (
                                <>
                                    <Button variant="outline" onClick={() => setStep('input')}>Back</Button>
                                    <Button onClick={generateAssertions}>
                                        <Check className="w-4 h-4" /> Add assertions
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
