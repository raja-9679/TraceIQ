// GitHub Action entrypoint: trigger a TraceIQ run for the current commit/PR,
// poll until complete, post (or update) a PR summary comment, and fail the
// job on regressions unless `fail-on: none`.
//
// Built with `npm run build` → dist/index.js (committed; actions must ship
// their compiled bundle).

const core = require('@actions/core');
const github = require('@actions/github');

const TERMINAL = new Set(['passed', 'failed', 'error']);
const FAILING = new Set(['failed', 'error']);
// Hidden marker that makes the PR comment idempotent: we find and update the
// existing comment instead of stacking a new one per push.
const COMMENT_MARKER = '<!-- traceiq-github-action -->';
const MAX_COMMENT_CHARS = 60000; // GitHub caps issue comments at 65536.

// ---------------------------------------------------------------------------
// TraceIQ REST helpers (X-API-Key auth)
// ---------------------------------------------------------------------------

async function http(method, url, headers, body) {
    const res = await fetch(url, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let parsed;
    try {
        parsed = text ? JSON.parse(text) : null;
    } catch {
        parsed = text;
    }
    if (!res.ok) {
        throw new Error(`TraceIQ ${method} ${url} failed: ${res.status} ${text}`);
    }
    return parsed;
}

function buildHeaders(apiKey, agentId) {
    return {
        'X-API-Key': apiKey,
        'X-Agent-Id': agentId,
        'Content-Type': 'application/json',
        'User-Agent': 'TraceIQ-GitHubAction/0.2',
    };
}

function detectGitContext() {
    const ctx = github.context;
    const sha = ctx.payload?.pull_request?.head?.sha || ctx.sha;
    const branch =
        ctx.payload?.pull_request?.head?.ref ||
        (ctx.ref || '').replace('refs/heads/', '');
    const repo = ctx.repo ? `${ctx.repo.owner}/${ctx.repo.repo}` : '';
    const prUrl = ctx.payload?.pull_request?.html_url || '';
    const prNumber = ctx.payload?.pull_request?.number || null;
    return { sha, branch, repo, prUrl, prNumber };
}

async function triggerRun(baseUrl, headers, suiteId, caseId, browser, gitCtx, triggeredBy, agentId) {
    const params = new URLSearchParams();
    params.set('suite_id', String(suiteId));
    if (caseId) params.set('case_id', String(caseId));
    for (const b of browser) params.append('browser', b);
    const url = `${baseUrl}/api/runs?${params.toString()}`;
    const body = {
        git_commit: gitCtx.sha,
        git_branch: gitCtx.branch,
        git_pr_url: gitCtx.prUrl,
        git_repo: gitCtx.repo,
        triggered_by: triggeredBy,
        agent_id: agentId,
    };
    return await http('POST', url, headers, body);
}

async function getRun(baseUrl, headers, runId) {
    return await http('GET', `${baseUrl}/api/runs/${runId}`, headers);
}

async function pollUntilDone(baseUrl, headers, runId, intervalSec, timeoutSec) {
    const deadline = Date.now() + timeoutSec * 1000;
    let last = null;
    while (Date.now() < deadline) {
        last = await getRun(baseUrl, headers, runId);
        const status = (last?.status || '').toLowerCase();
        core.info(`[TraceIQ] run ${runId} status=${status}`);
        if (TERMINAL.has(status)) return last;
        await new Promise((r) => setTimeout(r, intervalSec * 1000));
    }
    core.warning(`[TraceIQ] run ${runId} did not finish within ${timeoutSec}s.`);
    return last;
}

// ---------------------------------------------------------------------------
// Impact analysis (which tests are relevant to the PR diff, and why)
// ---------------------------------------------------------------------------

async function getChangedFiles(octokit, gitCtx) {
    const files = await octokit.paginate(octokit.rest.pulls.listFiles, {
        ...github.context.repo,
        pull_number: gitCtx.prNumber,
        per_page: 100,
    });
    return files.map((f) => f.filename);
}

// POST /api/runs/impact-analysis → { matched_cases: [{id, name,
// test_suite_id, is_ai_authored, matched_paths}], cases_without_code_paths,
// unmatched_files }. Reporting-only here: the comment explains which cases
// the diff touches; the run itself executes the configured suite.
async function runImpactAnalysis(baseUrl, headers, projectId, changedFiles) {
    return await http('POST', `${baseUrl}/api/runs/impact-analysis`, headers, {
        project_id: projectId,
        changed_files: changedFiles,
        include_no_code_paths: false,
    });
}

// ---------------------------------------------------------------------------
// PR comment
// ---------------------------------------------------------------------------

function statusIcon(status) {
    const s = (status || '').toLowerCase();
    if (s === 'passed') return ':white_check_mark:';
    if (s === 'failed') return ':x:';
    if (s === 'error') return ':exclamation:';
    return ':hourglass:';
}

function mdCell(text, maxLen = 200) {
    if (text === null || text === undefined) return '';
    let s = String(text).replace(/\r?\n/g, ' ').replace(/\|/g, '\\|').trim();
    if (s.length > maxLen) s = `${s.slice(0, maxLen - 1)}…`;
    return s;
}

function fmtDuration(ms) {
    if (ms === null || ms === undefined) return '';
    const sec = Number(ms) / 1000;
    return sec >= 60 ? `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s` : `${sec.toFixed(1)}s`;
}

function selectionSection(selection) {
    const lines = ['### Test selection', ''];
    if (selection.mode === 'impact') {
        const n = selection.matchedCases.length;
        lines.push(
            `Impact analysis of **${selection.changedFileCount}** changed file(s) ` +
            `matched **${n}** test case(s):`,
            '',
        );
        if (n > 0) {
            lines.push('| Test case | Why (matched changed files) |');
            lines.push('| --- | --- |');
            for (const c of selection.matchedCases) {
                const why = (c.matched_paths || []).map((p) => `\`${mdCell(p, 80)}\``).join(', ');
                lines.push(`| ${mdCell(c.name)} | ${why} |`);
            }
        }
        const notes = [];
        if (selection.unmatchedFiles.length > 0) {
            notes.push(`${selection.unmatchedFiles.length} changed file(s) matched no test case`);
        }
        if (selection.casesWithoutCodePaths > 0) {
            notes.push(`${selection.casesWithoutCodePaths} case(s) have no code-path mapping`);
        }
        if (notes.length) lines.push('', `_${notes.join('; ')}._`);
        lines.push('', `The full configured suite was executed; the table above shows which cases the diff touches.`);
    } else {
        lines.push(`Ran the configured suite${selection.suiteLabel ? ` **${mdCell(selection.suiteLabel)}**` : ''}.`);
        if (selection.reason) lines.push('', `_Impact analysis skipped: ${mdCell(selection.reason)}._`);
    }
    return lines;
}

function resultsSection(runs, baseUrl) {
    const lines = ['### Results', ''];
    for (const r of runs) {
        if (!r) continue;
        const status = (r.status || 'unknown').toLowerCase();
        const label = r.suite_name || r.test_case_name || `run ${r.id}`;
        const runUrl = `${baseUrl}/runs/${r.id}`;
        const meta = [];
        if (r.browser) meta.push(r.browser);
        if (r.git_commit) meta.push(`\`${String(r.git_commit).slice(0, 8)}\``);
        lines.push(
            `#### ${statusIcon(status)} [Run #${r.id}](${runUrl}) — ${mdCell(label)} — \`${status}\`` +
            (meta.length ? ` (${meta.join(', ')})` : ''),
        );
        const results = Array.isArray(r.results) ? r.results : [];
        if (results.length > 0) {
            lines.push('', '| Test case | Status | Duration | Details |', '| --- | --- | --- | --- |');
            for (const tc of results) {
                const tcStatus = (tc.status || '').toLowerCase();
                lines.push(
                    `| ${statusIcon(tcStatus)} ${mdCell(tc.test_name)} | \`${tcStatus}\` | ` +
                    `${fmtDuration(tc.duration_ms)} | ${mdCell(tc.error_message || '')} |`,
                );
            }
        } else {
            lines.push('', `passed: ${r.passed_tests ?? '?'} / failed: ${r.failed_tests ?? '?'} / total: ${r.total_tests ?? '?'}`);
            if (r.error_message) lines.push('', `> ${mdCell(r.error_message, 400)}`);
        }
        const links = [`[Open in TraceIQ](${runUrl})`];
        if (r.trace_url) links.push(`[Trace](${r.trace_url})`);
        if (r.video_url) links.push(`[Video](${r.video_url})`);
        lines.push('', links.join(' · '), '');
    }
    return lines;
}

function failureAnalysisSection(runs) {
    const blocks = [];
    for (const r of runs) {
        if (!r) continue;
        const ai = r.ai_analysis;
        if (!ai) continue;
        const summary = typeof ai === 'string' ? ai : ai.summary;
        const suggestions = typeof ai === 'object' ? ai.suggestions || [] : [];
        if (!summary && suggestions.length === 0) continue;
        blocks.push(`**Run #${r.id}:**`);
        if (summary) blocks.push(`> ${mdCell(summary, 1500)}`);
        for (const s of suggestions.slice(0, 5)) blocks.push(`- ${mdCell(s, 300)}`);
        blocks.push('');
    }
    if (blocks.length === 0) return [];
    return ['### Failure analysis', '', ...blocks];
}

function buildComment({ runs, baseUrl, selection, overall, passed, failed, gitCtx }) {
    const runCount = runs.filter(Boolean).length;
    const lines = [
        COMMENT_MARKER,
        '## TraceIQ regression results',
        '',
        `**Overall: ${overall === 'passed' ? ':white_check_mark: PASSED' : ':x: FAILED'}** — ` +
        `${passed} passed / ${failed} failed across ${runCount} run(s)` +
        (gitCtx.sha ? ` on commit \`${gitCtx.sha.slice(0, 8)}\`` : ''),
        '',
        ...selectionSection(selection),
        '',
        ...resultsSection(runs, baseUrl),
        ...failureAnalysisSection(runs),
    ];
    let body = lines.join('\n');
    if (body.length > MAX_COMMENT_CHARS) {
        body = `${body.slice(0, MAX_COMMENT_CHARS)}\n\n_… truncated; see TraceIQ for full results._`;
    }
    return body;
}

async function upsertPrComment(octokit, prNumber, body) {
    const repo = github.context.repo;
    const comments = await octokit.paginate(octokit.rest.issues.listComments, {
        ...repo,
        issue_number: prNumber,
        per_page: 100,
    });
    const existing = comments.find((c) => c.body && c.body.includes(COMMENT_MARKER));
    if (existing) {
        await octokit.rest.issues.updateComment({ ...repo, comment_id: existing.id, body });
        core.info(`[TraceIQ] Updated PR comment ${existing.id}.`);
    } else {
        const created = await octokit.rest.issues.createComment({
            ...repo,
            issue_number: prNumber,
            body,
        });
        core.info(`[TraceIQ] Posted PR comment ${created.data.id}.`);
    }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
    try {
        const baseUrl = core.getInput('base-url', { required: true }).replace(/\/$/, '');
        const apiKey = core.getInput('api-key', { required: true });
        const suiteId = parseInt(core.getInput('suite-id', { required: true }), 10);
        const caseIdRaw = core.getInput('case-id');
        const caseId = caseIdRaw ? parseInt(caseIdRaw, 10) : undefined;
        const projectIdRaw = core.getInput('project-id');
        const projectId = projectIdRaw ? parseInt(projectIdRaw, 10) : undefined;
        const browser = core.getInput('browser').split(',').map((s) => s.trim()).filter(Boolean);
        const agentId = core.getInput('agent-id') || 'github-action';
        const triggeredBy = core.getInput('triggered-by') || 'ci';
        const interval = parseInt(core.getInput('poll-interval-seconds') || '10', 10);
        const timeout = parseInt(core.getInput('timeout-seconds') || '1800', 10);
        // `fail-on`: none | failures. Legacy comma-separated status lists
        // ("failed,error") are treated as `failures`.
        const failOnRaw = (core.getInput('fail-on') || 'failures').trim().toLowerCase();
        const failOnNone = failOnRaw === 'none';
        const postComment = (core.getInput('post-pr-comment') || 'true').toLowerCase() === 'true';
        const githubToken = core.getInput('github-token') || process.env.GITHUB_TOKEN || '';

        const headers = buildHeaders(apiKey, agentId);
        const gitCtx = detectGitContext();
        const octokit = githubToken ? github.getOctokit(githubToken) : null;

        // --- Test selection reporting: impact analysis on the PR diff -----
        let selection = { mode: 'suite', suiteLabel: `suite ${suiteId}`, reason: null };
        if (!projectId) {
            selection.reason = 'no `project-id` input provided';
        } else if (!gitCtx.prNumber) {
            selection.reason = 'not running in a pull request context';
        } else if (!octokit) {
            selection.reason = 'no `github-token` available to read the PR diff';
        } else {
            try {
                const changedFiles = await getChangedFiles(octokit, gitCtx);
                const impact = await runImpactAnalysis(baseUrl, headers, projectId, changedFiles);
                selection = {
                    mode: 'impact',
                    changedFileCount: changedFiles.length,
                    matchedCases: impact.matched_cases || [],
                    unmatchedFiles: impact.unmatched_files || [],
                    casesWithoutCodePaths: impact.cases_without_code_paths || 0,
                };
                core.info(
                    `[TraceIQ] Impact analysis: ${selection.matchedCases.length} case(s) ` +
                    `matched across ${changedFiles.length} changed file(s).`,
                );
            } catch (err) {
                core.warning(`Impact analysis failed, falling back to suite report: ${err.message}`);
                selection = { mode: 'suite', suiteLabel: `suite ${suiteId}`, reason: `impact analysis failed (${err.message})` };
            }
        }

        // --- Trigger + poll ------------------------------------------------
        core.info(`[TraceIQ] Triggering run for suite ${suiteId} on commit ${gitCtx.sha}`);
        const created = await triggerRun(baseUrl, headers, suiteId, caseId, browser, gitCtx, triggeredBy, agentId);
        const createdRuns = Array.isArray(created) ? created : [created];
        const runIds = createdRuns.map((r) => r.id);
        core.setOutput('run-id', runIds.join(','));
        core.info(`[TraceIQ] Created run(s): ${runIds.join(', ')}`);

        const finalRuns = [];
        let timedOut = false;
        for (const rid of runIds) {
            const final = await pollUntilDone(baseUrl, headers, rid, interval, timeout);
            const st = (final?.status || '').toLowerCase();
            if (!TERMINAL.has(st)) timedOut = true;
            finalRuns.push(final);
        }
        if (selection.mode === 'suite' && finalRuns[0]?.suite_name) {
            selection.suiteLabel = finalRuns[0].suite_name;
        }

        const passed = finalRuns.reduce((acc, r) => acc + (r?.passed_tests || 0), 0);
        const failed = finalRuns.reduce((acc, r) => acc + (r?.failed_tests || 0), 0);
        const anyBad = timedOut ||
            finalRuns.some((r) => FAILING.has((r?.status || '').toLowerCase()));
        const overall = anyBad ? 'failed' : 'passed';

        core.setOutput('status', overall);
        core.setOutput('passed', String(passed));
        core.setOutput('failed', String(failed));

        // --- PR comment (idempotent via hidden marker) ---------------------
        if (postComment && gitCtx.prNumber && octokit) {
            try {
                const body = buildComment({ runs: finalRuns, baseUrl, selection, overall, passed, failed, gitCtx });
                await upsertPrComment(octokit, gitCtx.prNumber, body);
            } catch (err) {
                core.warning(`Failed to post PR comment: ${err.message}`);
            }
        } else if (postComment && !gitCtx.prNumber) {
            core.info('Not running on a pull request; skipping PR comment.');
        } else if (postComment && !octokit) {
            core.warning('No github-token input or GITHUB_TOKEN env; skipping PR comment.');
        }

        // --- Quality gate ---------------------------------------------------
        if (overall === 'failed') {
            const msg = timedOut
                ? `TraceIQ run did not finish within ${timeout}s.`
                : `TraceIQ regression failed: ${failed} test case(s) did not pass.`;
            if (failOnNone) {
                core.warning(`${msg} (not failing the step: fail-on=none)`);
            } else {
                core.setFailed(msg);
            }
        } else {
            core.info(`[TraceIQ] All runs passed (${passed} test cases).`);
        }
    } catch (err) {
        core.setFailed(err.message || String(err));
    }
}

main();
