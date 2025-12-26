import React, { useState } from 'react';
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Wand2, ArrowRight, Check, X, ChevronRight, ChevronDown } from "lucide-react";

interface AssertionRequest {
    type: 'xpath';
    key: string; // xpath
    operator: 'equals' | 'exists' | 'contains';
    value?: string;
}

interface FeedAssertionGeneratorModalProps {
    onGenerate: (assertions: AssertionRequest[]) => void;
}

interface XmlNode {
    id: string;
    tagName: string;
    textContent: string | null;
    path: string;
    children: XmlNode[];
    attributes: { name: string; value: string }[];
}

interface NodeSelection {
    selected: boolean;
    operator: 'exists' | 'equals' | 'contains';
}

export const FeedAssertionGeneratorModal: React.FC<FeedAssertionGeneratorModalProps> = ({ onGenerate }) => {
    const [open, setOpen] = useState(false);
    const [step, setStep] = useState<'input' | 'selection'>('input');
    const [xmlInput, setXmlInput] = useState('');
    const [rootNode, setRootNode] = useState<XmlNode | null>(null);
    const [selections, setSelections] = useState<Record<string, NodeSelection>>({});
    const [error, setError] = useState<string | null>(null);
    const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({});

    const parseXml = () => {
        try {
            const parser = new DOMParser();
            const doc = parser.parseFromString(xmlInput, "text/xml");
            const parseError = doc.querySelector("parsererror");
            if (parseError) {
                throw new Error(parseError.textContent || "XML Parse Error");
            }

            const root = buildNodeTree(doc.documentElement, "");
            setRootNode(root);
            setStep('selection');
            setError(null);
            // Expand root by default
            setExpandedNodes({ [root.id]: true });
        } catch (e: any) {
            setError("Invalid XML: " + e.message);
        }
    };

    const buildNodeTree = (element: Element, parentPath: string): XmlNode => {
        const id = crypto.randomUUID();
        // Simple XPath generation (not perfect for all cases but good for feeds)
        // We need to calculate index if there are siblings with same tag
        let index = 1;
        let sibling = element.previousElementSibling;
        while (sibling) {
            if (sibling.tagName === element.tagName) index++;
            sibling = sibling.previousElementSibling;
        }

        const tagName = element.tagName;
        // Handle namespaces in XPath: if tag has colon, use *[name()='tag'] syntax to avoid namespace prefix errors
        const xpathTag = tagName.includes(':') ? `*[name()='${tagName}']` : tagName;
        const currentPath = parentPath ? `${parentPath}/${xpathTag}[${index}]` : `/${xpathTag}`;

        const children: XmlNode[] = [];
        for (let i = 0; i < element.children.length; i++) {
            children.push(buildNodeTree(element.children[i], currentPath));
        }

        const attributes = [];
        for (let i = 0; i < element.attributes.length; i++) {
            attributes.push({
                name: element.attributes[i].name,
                value: element.attributes[i].value
            });
        }

        // Get direct text content (ignoring children text) if it's a leaf-ish node
        let textContent = null;
        if (children.length === 0) {
            textContent = element.textContent;
        }

        return {
            id,
            tagName,
            textContent,
            path: currentPath,
            children,
            attributes
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

    const updateOperator = (id: string, operator: 'exists' | 'equals' | 'contains') => {
        setSelections(prev => ({
            ...prev,
            [id]: {
                selected: true,
                operator
            }
        }));
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
                    key: node.path,
                    operator: selection.operator,
                    value: selection.operator !== 'exists' ? (node.textContent || '') : undefined
                });
            }
            node.children.forEach(traverse);
        };

        if (rootNode) traverse(rootNode);

        onGenerate(assertions);
        setOpen(false);
        setStep('input');
        setXmlInput('');
        setSelections({});
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
                        className="h-4 w-4 accent-slate-900 cursor-pointer"
                    />

                    <span className="text-xs font-mono text-blue-700 font-medium">
                        &lt;{node.tagName}&gt;
                    </span>

                    {node.textContent && (
                        <span className="text-xs text-slate-600 truncate max-w-[150px]" title={node.textContent}>
                            {node.textContent}
                        </span>
                    )}

                    {selection.selected && (
                        <div className="ml-auto">
                            <Select
                                value={selection.operator}
                                onValueChange={(v: any) => updateOperator(node.id, v)}
                            >
                                <SelectTrigger className="h-6 w-[100px] text-xs">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="exists">Exists</SelectItem>
                                    <SelectItem value="equals">Equals</SelectItem>
                                    <SelectItem value="contains">Contains</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    )}
                </div>

                {isExpanded && node.children.map(child => renderNode(child, depth + 1))}
            </div>
        );
    };

    return (
        <>
            <Button variant="outline" size="sm" className="text-xs h-6" onClick={() => setOpen(true)}>
                <Wand2 className="w-3 h-3 mr-1" /> Generate Assertions
            </Button>

            {open && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                    <div className="bg-white rounded-lg shadow-lg w-full max-w-3xl max-h-[80vh] flex flex-col m-4">
                        <div className="flex items-center justify-between p-4 border-b">
                            <h2 className="text-lg font-semibold">Generate Feed Assertions</h2>
                            <Button variant="ghost" size="icon" onClick={() => setOpen(false)}>
                                <X className="w-4 h-4" />
                            </Button>
                        </div>

                        <div className="flex-1 overflow-hidden flex flex-col min-h-[300px] p-4">
                            {step === 'input' ? (
                                <div className="flex-1 flex flex-col gap-2">
                                    <p className="text-sm text-slate-500">Paste your XML/RSS Feed content below.</p>
                                    <textarea
                                        className="flex-1 font-mono text-xs p-4 border rounded-md resize-none focus:outline-none focus:ring-2 focus:ring-slate-400"
                                        placeholder='<?xml version="1.0" ...'
                                        value={xmlInput}
                                        onChange={e => setXmlInput(e.target.value)}
                                    />
                                    {error && <p className="text-xs text-red-500">{error}</p>}
                                </div>
                            ) : (
                                <div className="flex-1 flex flex-col gap-2 overflow-hidden">
                                    <p className="text-sm text-slate-500">Select nodes to generate assertions for.</p>
                                    <div className="border rounded-md flex-1 overflow-hidden flex flex-col">
                                        <div className="bg-slate-100 p-2 border-b text-xs font-medium text-slate-500 px-4 flex justify-between">
                                            <span>XML Structure</span>
                                            <span>Assertion Type</span>
                                        </div>
                                        <div className="flex-1 overflow-y-auto p-2">
                                            {rootNode && renderNode(rootNode, 0)}
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="p-4 border-t flex justify-end gap-2">
                            {step === 'input' ? (
                                <Button onClick={parseXml} disabled={!xmlInput.trim()}>
                                    Next <ArrowRight className="w-4 h-4 ml-2" />
                                </Button>
                            ) : (
                                <>
                                    <Button variant="ghost" onClick={() => setStep('input')}>Back</Button>
                                    <Button onClick={generateAssertions}>
                                        <Check className="w-4 h-4 mr-2" /> Add Assertions
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
