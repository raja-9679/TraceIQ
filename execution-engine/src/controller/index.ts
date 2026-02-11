/**
 * Execution Controller - Orchestration & Intelligence Layer
 * 
 * Responsibilities:
 * 1. Monitor test execution across all workers
 * 2. Aggregate and curate final results per run
 * 3. AI-powered analysis of console logs/errors (OPTIONAL)
 * 4. Send curated results to Backend for storage & notifications
 * 5. Provide real-time metrics and monitoring API
 * 
 * NOTE: Notifications (email, Slack, etc.) are handled by Backend
 * because it has access to user preferences and notification settings.
 */

import { RunMonitor } from './run-monitor';
import { AIAnalyzer } from './ai-analyzer';
import { MetricsCollector } from './metrics-collector';

export class ExecutionController {
    private runMonitor: RunMonitor;
    private aiAnalyzer: AIAnalyzer | null;
    private metricsCollector: MetricsCollector;
    private aiEnabled: boolean;

    constructor() {
        this.runMonitor = new RunMonitor();
        this.metricsCollector = new MetricsCollector();
        
        // AI analysis is optional
        this.aiEnabled = process.env.AI_ANALYSIS_ENABLED === 'true';
        this.aiAnalyzer = this.aiEnabled ? new AIAnalyzer() : null;
        
        if (!this.aiEnabled) {
            console.log('[Controller] AI analysis disabled (set AI_ANALYSIS_ENABLED=true to enable)');
        }
    }

    async start(): Promise<void> {
        console.log('[Controller] Starting Execution Controller...');
        
        // Start monitoring completed runs
        await this.runMonitor.start();
        
        // Subscribe to run completion events
        this.runMonitor.on('runCompleted', async (runData) => {
            await this.handleRunCompletion(runData);
        });

        // Start metrics collection
        await this.metricsCollector.start();

        console.log('[Controller] Execution Controller started');
    }

    /**
     * Handle completed test run - curate results, analyze (optional), send to backend
     */
    private async handleRunCompletion(runData: any): Promise<void> {
        const { runId, results, summary } = runData;
        
        console.log(`[Controller] Processing completed run ${runId}`);

        try {
            // 1. Curate and format final results
            const curatedResults = await this.curateResults(runId, results, summary);

            // 2. AI Analysis (OPTIONAL - only if enabled and failures exist)
            if (this.aiEnabled && this.aiAnalyzer && (summary.failed > 0 || summary.errors > 0)) {
                console.log(`[Controller] Running AI analysis for run ${runId}...`);
                try {
                    const analysis = await this.aiAnalyzer.analyzeFailures(runId, results);
                    curatedResults.aiAnalysis = analysis;
                } catch (aiErr) {
                    console.error(`[Controller] AI analysis failed for run ${runId}:`, aiErr);
                    // Continue without AI analysis - don't block the flow
                }
            }

            // 3. Send curated results to Backend
            // Backend will handle: DB storage, notifications, user preferences
            await this.sendToBackend(runId, curatedResults);

            console.log(`[Controller] Run ${runId} processing complete`);
        } catch (err) {
            console.error(`[Controller] Error processing run ${runId}:`, err);
        }
    }

    /**
     * Curate and format final results for a run
     */
    private async curateResults(runId: number, results: any[], summary: any): Promise<any> {
        return {
            runId,
            summary: {
                total: summary.total,
                passed: summary.passed,
                failed: summary.failed,
                duration: summary.duration,
                status: summary.failed > 0 ? 'FAILED' : 'PASSED'
            },
            testResults: results.map(r => ({
                testName: r.test_name,
                status: r.status,
                duration: r.duration_ms,
                error: r.error,
                videoUrl: r.video_url,
                traceUrl: r.trace_url
            })),
            completedAt: new Date().toISOString()
        };
    }

    /**
     * Send curated results to Backend for storage and notifications
     */
    private async sendToBackend(runId: number, curatedResults: any): Promise<void> {
        const backendUrl = process.env.BACKEND_URL || 'http://backend:8000';
        
        try {
            const response = await fetch(`${backendUrl}/api/runs/${runId}/finalize`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Internal-Service': 'execution-controller'
                },
                body: JSON.stringify(curatedResults)
            });

            if (!response.ok) {
                console.error(`[Controller] Backend rejected finalize for run ${runId}: ${response.status}`);
            } else {
                console.log(`[Controller] Sent curated results to backend for run ${runId}`);
            }
        } catch (err) {
            console.error(`[Controller] Failed to send results to backend for run ${runId}:`, err);
            // Results are already in DB from result_aggregator, this is just for AI analysis + notifications
        }
    }

    async stop(): Promise<void> {
        await this.runMonitor.stop();
        await this.metricsCollector.stop();
        console.log('[Controller] Execution Controller stopped');
    }
}

export { RunMonitor } from './run-monitor';
export { AIAnalyzer } from './ai-analyzer';
export { MetricsCollector } from './metrics-collector';
