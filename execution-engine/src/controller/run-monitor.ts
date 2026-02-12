/**
 * Run Monitor - Monitors completed runs from Redis
 * 
 * Listens to:
 * - Run completion events
 * - Aggregates all test results for a run
 * - Emits events for controller to process
 */

import Redis from 'ioredis';
import { EventEmitter } from 'events';

const RESULTS_STREAM = process.env.REDIS_RESULTS_STREAM || 'jobs:results';
const RUN_COMPLETION_CHANNEL = 'run:*:complete';

export class RunMonitor extends EventEmitter {
    private redis: Redis;
    private subscriber: Redis;
    private isRunning: boolean = false;

    constructor() {
        super();
        const redisUrl = process.env.REDIS_URL || 'redis://localhost:6379';
        this.redis = new Redis(redisUrl);
        this.subscriber = new Redis(redisUrl);
    }

    async start(): Promise<void> {
        if (this.isRunning) return;
        this.isRunning = true;

        console.log('[RunMonitor] Starting run monitoring...');

        // Subscribe to run completion events (pattern subscribe)
        await this.subscriber.psubscribe('run:*:complete');
        
        this.subscriber.on('pmessage', async (pattern, channel, message) => {
            try {
                const runId = this.extractRunId(channel);
                if (runId) {
                    await this.handleRunCompletion(runId, JSON.parse(message));
                }
            } catch (err) {
                console.error('[RunMonitor] Error handling completion:', err);
            }
        });

        console.log('[RunMonitor] Run monitoring started');
    }

    private extractRunId(channel: string): number | null {
        // channel format: "run:123:complete"
        const match = channel.match(/run:(\d+):complete/);
        return match ? parseInt(match[1]) : null;
    }

    private async handleRunCompletion(runId: number, completionData: any): Promise<void> {
        console.log(`[RunMonitor] Run ${runId} completed, gathering results...`);

        try {
            // Fetch full results from database via API
            const results = await this.fetchRunResults(runId);
            const consoleLogs = await this.fetchConsoleLogs(runId);
            const networkEvents = await this.fetchNetworkEvents(runId);

            // Emit event with full data for controller
            this.emit('runCompleted', {
                runId,
                results,
                consoleLogs,
                networkEvents,
                summary: {
                    total: completionData.total,
                    passed: completionData.passed,
                    failed: completionData.failed,
                    duration: results.reduce((sum: number, r: any) => sum + (r.duration_ms || 0), 0)
                }
            });
        } catch (err) {
            console.error(`[RunMonitor] Failed to gather results for run ${runId}:`, err);
        }
    }

    private async fetchRunResults(runId: number): Promise<any[]> {
        // Fetch from backend API
        try {
            const response = await fetch(`http://backend:8000/api/runs/${runId}/results`);
            if (response.ok) {
                const data = await response.json();
                return data.results || [];
            }
        } catch (err) {
            console.error(`[RunMonitor] Failed to fetch results for run ${runId}:`, err);
        }
        return [];
    }

    private async fetchConsoleLogs(runId: number): Promise<any[]> {
        // Fetch console logs - stored in run's metadata or separate collection
        try {
            const response = await fetch(`http://backend:8000/api/runs/${runId}/logs`);
            if (response.ok) {
                const data = await response.json();
                return data.logs || [];
            }
        } catch (err) {
            // Logs endpoint might not exist yet
        }
        return [];
    }

    private async fetchNetworkEvents(runId: number): Promise<any[]> {
        // Network events are stored in the run
        try {
            const response = await fetch(`http://backend:8000/api/runs/${runId}`);
            if (response.ok) {
                const data = await response.json();
                return data.network_events || [];
            }
        } catch (err) {
            console.error(`[RunMonitor] Failed to fetch network events for run ${runId}:`, err);
        }
        return [];
    }

    /**
     * Get current status of all active runs
     */
    async getActiveRuns(): Promise<any[]> {
        const keys = await this.redis.keys('runs:*:progress');
        const activeRuns = [];

        for (const key of keys) {
            const runId = key.split(':')[1];
            const progress = await this.redis.hgetall(key);
            if (progress && progress.status !== 'completed') {
                activeRuns.push({
                    runId: parseInt(runId),
                    ...progress
                });
            }
        }

        return activeRuns;
    }

    /**
     * Get progress for a specific run
     */
    async getRunProgress(runId: number): Promise<any> {
        return this.redis.hgetall(`runs:${runId}:progress`);
    }

    async stop(): Promise<void> {
        this.isRunning = false;
        await this.subscriber.punsubscribe();
        await this.subscriber.quit();
        await this.redis.quit();
        console.log('[RunMonitor] Run monitoring stopped');
    }
}
