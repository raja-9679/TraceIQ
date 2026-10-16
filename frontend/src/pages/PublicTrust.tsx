import { useState, type FormEvent, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Download, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/redesign/primitives';
import { LogoMark } from '@/components/redesign-local/groupF/AuthLayout';
import { inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import { fetchPublicTrust, fetchTrustDocument, requestTrustAccess, type PublicTrustPage } from '@/api/trustCenter';
import { INCIDENT_STATUS, OVERALL, POSTURE_TONE, asUtc, fmtBytes, tokenFromHash } from '@/components/trust/trustHelpers';

// Public, unauthenticated trust center, rendered outside the app shell.
// Data: GET /api/trust/{slug} (a reduced, admin-curated view). A document
// link is /trust/<slug>#<token>: the fragment never reaches a server, and
// the token is POSTed in the body for a short-lived download URL.

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
    return (
        <section aria-labelledby={id} className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6">
            <h2 id={id} className="text-base font-semibold text-ink">{title}</h2>
            {children}
        </section>
    );
}

const fmtUptime = (v: number | null) => (v == null ? '—' : `${v.toFixed(2)}%`);

function DocumentDownload({ slug, token }: { slug: string; token: string }) {
    const dl = useMutation({ mutationFn: () => fetchTrustDocument(slug, token) });
    const d = dl.data;
    return (
        <section aria-label="Shared document" className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6 shadow-[inset_4px_0_0_#8FD3B6]">
            {dl.isError ? (
                <>
                    <p className="text-base font-semibold text-ink">This document link is not available</p>
                    <p className="text-sm text-ink-soft">It may have expired or been revoked. Ask whoever sent it for a new one.</p>
                </>
            ) : d ? (
                <>
                    <p className="text-base font-semibold text-ink">{d.title}</p>
                    <p className="text-xs text-ink-soft">{d.filename} · {fmtBytes(d.size_bytes)} · SHA-256 <span className="break-all font-mono">{d.sha256}</span></p>
                    <div><Button asChild><a href={d.url} rel="noreferrer"><Download className="h-4 w-4" /> Download now</a></Button></div>
                    <p className="text-xs text-ink-soft">The download link works for {d.url_expires_in} seconds. Your access link expires {new Date(d.link_expires_at).toLocaleDateString()}.</p>
                </>
            ) : (
                <>
                    <p className="text-base font-semibold text-ink">A document has been shared with you</p>
                    <p className="text-sm text-ink-soft">Downloads are recorded for the organisation that shared it.</p>
                    <div><Button onClick={() => dl.mutate()} disabled={dl.isPending}>Get the document</Button></div>
                </>
            )}
        </section>
    );
}

function AccessRequestForm({ slug, docs }: { slug: string; docs: PublicTrustPage['documents'] }) {
    const [name, setName] = useState('');
    const [email, setEmail] = useState('');
    const [company, setCompany] = useState('');
    const [message, setMessage] = useState('');
    const [picked, setPicked] = useState<number[]>([]);
    const send = useMutation({
        mutationFn: () => requestTrustAccess(slug, { name, email, company: company || undefined, message: message || undefined, document_ids: picked }),
    });
    const submit = (e: FormEvent) => { e.preventDefault(); send.mutate(); };
    if (send.isSuccess) {
        return <p className="text-sm text-ink" role="status">Thanks. Your request was sent to the team; they will reply by email if they agree.</p>;
    }
    return (
        <form onSubmit={submit} className="flex flex-col gap-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm font-medium text-ink">Name
                    <Input required maxLength={120} className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
                </label>
                <label className="flex flex-col gap-1 text-sm font-medium text-ink">Work email
                    <Input required type="email" maxLength={254} className={inputClass} value={email} onChange={(e) => setEmail(e.target.value)} />
                </label>
                <label className="flex flex-col gap-1 text-sm font-medium text-ink">Company (optional)
                    <Input maxLength={120} className={inputClass} value={company} onChange={(e) => setCompany(e.target.value)} />
                </label>
            </div>
            {docs.length > 0 && (
                <fieldset className="flex flex-col gap-1">
                    <legend className="mb-1 text-sm font-medium text-ink">Documents you need</legend>
                    {docs.map((d) => (
                        <label key={d.id} className="flex min-h-10 items-center gap-3 text-sm text-ink">
                            <input type="checkbox" className="h-[18px] w-[18px] accent-ink" checked={picked.includes(d.id)}
                                onChange={(e) => setPicked((p) => (e.target.checked ? [...p, d.id] : p.filter((x) => x !== d.id)))} />
                            {d.title}
                        </label>
                    ))}
                </fieldset>
            )}
            <label className="flex flex-col gap-1 text-sm font-medium text-ink">Message (optional)
                <textarea rows={3} maxLength={2000} className={`${textareaClass} font-sans`} value={message} onChange={(e) => setMessage(e.target.value)} />
            </label>
            {send.isError && <p className="text-sm text-ink" role="alert">Could not send the request. Check the email address, or try again later.</p>}
            <div><Button type="submit" disabled={send.isPending}>Request access</Button></div>
        </form>
    );
}

export default function PublicTrust() {
    const { slug = '' } = useParams();
    const token = tokenFromHash(window.location.hash);
    // No retries: a 404 is the answer (off, unknown), and the route is rate-limited.
    const q = useQuery({ queryKey: ['public-trust', slug], queryFn: () => fetchPublicTrust(slug), retry: false, refetchOnWindowFocus: false });
    const data = q.data;

    if (q.isLoading) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-background" aria-busy="true">
                <RefreshCw className="h-7 w-7 animate-spin text-ink-muted" aria-label="Loading" />
            </div>
        );
    }
    if (q.isError || !data) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-background px-4">
                <div className="flex max-w-sm flex-col items-center gap-2 rounded-3xl border border-border bg-white px-6 py-12 text-center">
                    <p className="text-lg font-semibold text-ink">This page is not available</p>
                    <p className="text-sm text-ink-soft">The link may be wrong, or the page has been switched off.</p>
                </div>
            </div>
        );
    }
    const overall = data.status ? OVERALL[data.status.overall] ?? OVERALL.unknown : null;

    return (
        <div className="min-h-screen bg-background px-4 py-10 sm:py-14">
            <div className="mx-auto flex max-w-4xl flex-col gap-6">
                <header className="flex flex-col gap-2">
                    <h1 className="text-[28px] font-bold leading-tight tracking-[-0.03em] text-ink">{data.title}</h1>
                    {data.intro && <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-ink">{data.intro}</p>}
                    {data.contact_email && (
                        <p className="text-sm text-ink-soft">Security reports: <a className="font-medium text-ink underline" href={`mailto:${data.contact_email}`}>{data.contact_email}</a></p>
                    )}
                </header>

                {token && <DocumentDownload slug={slug} token={token} />}

                {data.status && overall && (
                    <Section id="trust-status" title="System status">
                        <div><Pill tone={overall.tone}>{overall.label}</Pill></div>
                        {data.status.monitors.length > 0 && (
                            <ul className="flex flex-col">
                                {data.status.monitors.map((m, i) => (
                                    <li key={`${m.name}-${i}`} className={`flex min-h-11 flex-wrap items-center justify-between gap-3 py-2 text-sm ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                                        <span className="min-w-0 truncate text-ink">{m.name}</span>
                                        <span className="flex items-center gap-3 text-xs text-ink-soft tabular-nums">
                                            <span>24h {fmtUptime(m.uptime_24h)}</span>
                                            <span>7d {fmtUptime(m.uptime_7d)}</span>
                                            <Pill tone={m.state === 'up' ? 'mint' : m.state === 'down' ? 'peach' : 'neutral'}>{m.state === 'up' ? 'Up' : m.state === 'down' ? 'Down' : 'No data'}</Pill>
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </Section>
                )}

                {data.incidents.length > 0 && (
                    <Section id="trust-incidents" title="Incidents">
                        <ul className="flex flex-col gap-4">
                            {data.incidents.map((p) => {
                                const st = INCIDENT_STATUS[p.status];
                                return (
                                    <li key={p.id} className="flex flex-col gap-2 border-t border-[#F0EEE8] pt-4 first:border-t-0 first:pt-0">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h3 className="text-[15px] font-semibold text-ink">{p.title}</h3>
                                            {st && <Pill tone={st.tone}>{st.label}</Pill>}
                                        </div>
                                        {p.components.length > 0 && <p className="text-xs text-ink-soft">Affected: {p.components.join(', ')}</p>}
                                        <ol className="flex flex-col gap-2">
                                            {p.updates.map((u, i) => (
                                                <li key={`${u.at}-${i}`} className="flex flex-col gap-0.5 text-sm">
                                                    <span className="text-xs text-ink-soft">{new Date(asUtc(u.at)).toLocaleString()} · {INCIDENT_STATUS[u.status]?.label ?? u.status}</span>
                                                    {/* body_html is rendered server-side escape-first (only p/br/ul/li/strong/em/code);
                                                        see app/services/trust_center.render_markdown_safe. */}
                                                    <div className="text-ink [&_code]:rounded [&_code]:bg-background [&_code]:px-1 [&_ul]:list-disc [&_ul]:pl-5" dangerouslySetInnerHTML={{ __html: u.body_html }} />
                                                </li>
                                            ))}
                                        </ol>
                                    </li>
                                );
                            })}
                        </ul>
                    </Section>
                )}

                {data.posture.length > 0 && (
                    <Section id="trust-posture" title="Security posture">
                        <ul className="flex flex-col">
                            {data.posture.map((item, i) => (
                                <li key={item.key} className={`flex min-h-11 flex-wrap items-center justify-between gap-3 py-2 text-sm ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                                    <span className="text-ink">{item.label}</span>
                                    <Pill tone={POSTURE_TONE[item.state] ?? 'neutral'}>{item.value}</Pill>
                                </li>
                            ))}
                        </ul>
                    </Section>
                )}

                {(data.documents.length > 0 || data.access_requests) && (
                    <Section id="trust-docs" title="Documents">
                        {data.documents.length > 0 && (
                            <ul className="flex flex-col">
                                {data.documents.map((d, i) => (
                                    <li key={d.id} className={`flex flex-col py-2 text-sm ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                                        <span className="font-medium text-ink">{d.title}</span>
                                        {d.description && <span className="text-xs text-ink-soft">{d.description}</span>}
                                    </li>
                                ))}
                            </ul>
                        )}
                        <p className="text-xs text-ink-soft">Documents are shared on request through a personal link.</p>
                        {data.access_requests && (
                            <div className="flex flex-col gap-2 border-t border-border pt-3">
                                <h3 className="text-sm font-semibold text-ink">Request access</h3>
                                <AccessRequestForm slug={slug} docs={data.documents} />
                            </div>
                        )}
                    </Section>
                )}

                <footer className="flex flex-col items-center gap-3 pt-2 text-center text-xs text-ink-soft">
                    <span>Updated {new Date(asUtc(data.generated_at)).toLocaleString()}</span>
                    <LogoMark />
                </footer>
            </div>
        </div>
    );
}
