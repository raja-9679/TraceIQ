// GitHub Action entrypoint: trigger a TraceIQ run for the current commit/PR,
// poll until complete, post a summary comment, and fail the job if the run
// ended in a status listed in `fail-on`.
//
// Built with `npm run build` → dist/index.js. The repo ships only sources;
// the dist bundle is produced at release time.

const core = require('@actions/core');
const github = require('@actions/github');

const TERMINAL = new Set(['passed', 'failed', 'error']);

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
        'User-Agent': 'TraceIQ-GitHubAction/0.1',
    };
}

function detectGitContext() {
    const ctx = github.context;
    const sha = ctx.payload?.pull_request?.head?.sha || ctx.sha;
    const branch = (ctx.ref || '').replace('refs/heads/', '');
    const repo = ctx.repo ? `${ctx.repo.owner}/${ctx.repo.repo}` : '';
    const prUrl = ctx.payload?.pull_request?.html_url || '';
    return { sha, branch, repo, prUrl };
}

async function triggerRun(baseUrl, headers, suiteId, caseId, browser, gitCtx, triggeredBy) {
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
    return last;
}

function formatPrComment(runs, baseUrl) {
    const lines = ['## TraceIQ regression results', ''];
    for (const r of runs) {
        const icon =
            r.status === 'passed' ? ':white_check_mark:' :
            r.status === 'failed' ? ':x:' : ':warning:';
        lines.push(`- ${icon} **${r.suite_name || `run ${r.id}`}** — \`${r.status}\``);
        lines.push(`  - passed: ${r.passed_tests ?? '?'} / failed: ${r.failed_tests ?? '?'} / total: ${r.total_tests ?? '?'}`);
        if (r.git_commit) lines.push(`  - commit: \`${r.git_commit.slice(0, 8)}\``);
        if (r.trace_url) lines.push(`  - [trace](${r.trace_url})`);
        lines.push(`  - [open in TraceIQ](${baseUrl}/runs/${r.id})`);
    }
    return lines.join('\n');
}

async function postPrComment(commentBody) {
    const token = process.env.GITHUB_TOKEN;
    if (!token) {
        core.warning('GITHUB_TOKEN not available; skipping PR comment.');
        return;
    }
    const ctx = github.context;
    const prNumber = ctx.payload?.pull_request?.number;
    if (!prNumber) {
        core.info('Not running on a pull request; skipping PR comment.');
        return;
    }
    const octokit = github.getOctokit(token);
    await octokit.rest.issues.createComment({
        ...ctx.repo,
        issue_number: prNumber,
        body: commentBody,
    });
}

async function main() {
    try {
        const baseUrl = core.getInput('base-url', { required: true }).replace(/\/$/, '');
        const apiKey = core.getInput('api-key', { required: true });
        const suiteId = parseInt(core.getInput('suite-id', { required: true }), 10);
        const caseIdRaw = core.getInput('case-id');
        const caseId = caseIdRaw ? parseInt(caseIdRaw, 10) : undefined;
        const browser = core.getInput('browser').split(',').map((s) => s.trim()).filter(Boolean);
        const agentId = core.getInput('agent-id') || 'github-action';
        const triggeredBy = core.getInput('triggered-by') || 'ci';
        const interval = parseInt(core.getInput('poll-interval-seconds') || '10', 10);
        const timeout = parseInt(core.getInput('timeout-seconds') || '1800', 10);
        const failOn = (core.getInput('fail-on') || 'failed,error').split(',').map((s) => s.trim());
        const postComment = (core.getInput('post-pr-comment') || 'true').toLowerCase() === 'true';

        const headers = buildHeaders(apiKey, agentId);
        const gitCtx = detectGitContext();
        core.info(`[TraceIQ] Triggering run for suite ${suiteId} on commit ${gitCtx.sha}`);

        const created = await triggerRun(baseUrl, headers, suiteId, caseId, browser, gitCtx, triggeredBy);
        const createdRuns = Array.isArray(created) ? created : [created];
        const runIds = createdRuns.map((r) => r.id);
        core.setOutput('run-id', runIds.join(','));
        core.info(`[TraceIQ] Created run(s): ${runIds.join(', ')}`);

        const finalRuns = [];
        for (const rid of runIds) {
            const final = await pollUntilDone(baseUrl, headers, rid, interval, timeout);
            finalRuns.push(final);
        }

        const passed = finalRuns.reduce((acc, r) => acc + (r?.passed_tests || 0), 0);
        const failed = finalRuns.reduce((acc, r) => acc + (r?.failed_tests || 0), 0);
        const overall = finalRuns.some((r) => failOn.includes((r?.status || '').toLowerCase()))
            ? 'failed'
            : 'passed';

        core.setOutput('status', overall);
        core.setOutput('passed', String(passed));
        core.setOutput('failed', String(failed));

        if (postComment) {
            try {
                await postPrComment(formatPrComment(finalRuns, baseUrl));
            } catch (err) {
                core.warning(`Failed to post PR comment: ${err.message}`);
            }
        }

        if (overall === 'failed') {
            core.setFailed(`TraceIQ regression failed: ${failed} test case(s) did not pass.`);
        } else {
            core.info(`[TraceIQ] All runs passed (${passed} test cases).`);
        }
    } catch (err) {
        core.setFailed(err.message || String(err));
    }
}

main();
