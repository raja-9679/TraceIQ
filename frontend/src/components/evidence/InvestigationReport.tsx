import { Link } from 'react-router-dom';
import { Eye, GitCommitHorizontal, History, Lightbulb } from 'lucide-react';
import {
    evidenceLabel, refChipText, runIdOfRef, shortCommit, type FailureReportV2,
} from './evidenceHelpers';

// FailureReport v2 body: "Observed" (facts) kept apart from "Likely cause"
// (ranked hypotheses), each with clickable evidence references. A step ref
// calls `onSelectEvidence`, which the run page turns into "expand that result
// and scroll to the step"; a `run:<id>` ref links to that earlier run.

const CATEGORY_STYLE: Record<string, string> = {
    app_bug: 'bg-rose-100 text-rose-700 border-rose-200',
    test_bug: 'bg-amber-100 text-amber-700 border-amber-200',
    environment: 'bg-sky-100 text-sky-700 border-sky-200',
    flake: 'bg-slate-100 text-slate-600 border-slate-200',
    unknown: 'bg-slate-100 text-slate-500 border-slate-200',
};

function RefChip({ report, refId, onSelect }: {
    report: FailureReportV2; refId: string; onSelect: (ref: string) => void;
}) {
    const title = evidenceLabel(report, refId);
    const cls = 'inline-flex items-center text-[10px] font-mono px-1.5 py-0.5 rounded border border-indigo-200 '
        + 'bg-white text-indigo-700 hover:bg-indigo-50 hover:border-indigo-300 transition-colors';
    const runId = runIdOfRef(refId);
    if (runId !== null) {
        return <Link to={`/runs/${runId}`} className={cls} title={title}>{refChipText(refId)}</Link>;
    }
    return (
        <button type="button" onClick={() => onSelect(refId)} className={cls} title={title}>
            {refChipText(refId)}
        </button>
    );
}

function Refs({ report, refs, onSelect }: {
    report: FailureReportV2; refs: string[]; onSelect: (ref: string) => void;
}) {
    if (!refs.length) return null;
    return (
        <span className="inline-flex flex-wrap gap-1 ml-1.5 align-middle">
            {refs.map((r) => <RefChip key={r} report={report} refId={r} onSelect={onSelect} />)}
        </span>
    );
}

export function InvestigationReport({ report, onSelectEvidence }: {
    report: FailureReportV2;
    onSelectEvidence: (ref: string) => void;
}) {
    const observed = report.observed ?? [];
    const hypotheses = report.hypotheses ?? [];
    const history = report.history;
    const change = report.changed_since_last_pass;
    return (
        <div className="space-y-3">
            {observed.length > 0 && (
                <section>
                    <h4 className="text-[11px] font-bold text-slate-600 uppercase tracking-wider flex items-center gap-1.5 mb-1.5">
                        <Eye size={13} /> Observed
                    </h4>
                    <ul className="space-y-1">
                        {observed.map((o, i) => (
                            <li key={i} className="text-xs text-slate-700 leading-relaxed">
                                {o.text}
                                <Refs report={report} refs={o.evidence_refs} onSelect={onSelectEvidence} />
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            {hypotheses.length > 0 && (
                <section>
                    <h4 className="text-[11px] font-bold text-indigo-700 uppercase tracking-wider flex items-center gap-1.5 mb-1.5">
                        <Lightbulb size={13} /> Likely cause
                    </h4>
                    <ol className="space-y-1.5">
                        {hypotheses.map((h, i) => (
                            <li key={i} className={`text-xs rounded-md px-2 py-1.5 ${i === 0 ? 'bg-indigo-50/80 border border-indigo-100' : ''}`}>
                                <div className="flex items-center gap-2 flex-wrap">
                                    <span className="font-semibold text-slate-500 tabular-nums">{i + 1}.</span>
                                    <span className={`text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-full border ${CATEGORY_STYLE[h.category] || CATEGORY_STYLE.unknown}`}>
                                        {h.category.replace('_', ' ')}
                                    </span>
                                    <span className="text-[10px] font-mono text-indigo-500 tabular-nums">{Math.round(h.confidence * 100)}%</span>
                                    {h.source !== 'heuristic' && (
                                        <span className="text-[10px] font-mono text-indigo-300">{h.source}</span>
                                    )}
                                </div>
                                <p className="mt-0.5 text-slate-800">
                                    {h.summary}
                                    <Refs report={report} refs={h.evidence_refs} onSelect={onSelectEvidence} />
                                </p>
                            </li>
                        ))}
                    </ol>
                </section>
            )}

            {(history?.runs_considered || change) ? (
                <section className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600">
                    {history && history.runs_considered > 0 && (
                        <span className="flex items-center gap-1">
                            <History size={12} aria-hidden />
                            Failed {history.prior_failures}/{history.runs_considered} previous runs
                            {history.failing_streak > 1 && <> · {history.failing_streak} in a row</>}
                            {history.first_failing_commit && history.failing_streak > 1 && (
                                <> · since <span className="font-mono">{shortCommit(history.first_failing_commit)}</span></>
                            )}
                            {history.cluster && <> · cluster seen {history.cluster.occurrence_count}×</>}
                        </span>
                    )}
                    {change && (
                        <span className="flex items-center gap-1">
                            <GitCommitHorizontal size={12} aria-hidden />
                            Changed since last pass:{' '}
                            {change.compare_url ? (
                                <a href={change.compare_url} target="_blank" rel="noreferrer" className="font-mono text-indigo-700 hover:underline">
                                    {shortCommit(change.from_commit)}…{shortCommit(change.to_commit)}
                                </a>
                            ) : (
                                <span className="font-mono">{shortCommit(change.from_commit)}…{shortCommit(change.to_commit)}</span>
                            )}
                        </span>
                    )}
                </section>
            ) : null}
            {(report.dropped_citations ?? 0) > 0 && (
                <p className="text-[10px] text-slate-400">
                    {report.dropped_citations} AI citation(s) referred to evidence that does not exist and were removed.
                </p>
            )}
        </div>
    );
}
