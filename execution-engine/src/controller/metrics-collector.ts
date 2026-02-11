/**
 * Metrics Collector - Collects and exposes execution metrics
 * 
 * Metrics:
 * 1. Worker health and status
 * 2. Queue depth and processing rate
 * 3. Test pass/fail rates
 * 4. Average execution times
 * 5. System resource usage
 */

import Redis from 'ioredis';

export interface WorkerStatus {
    workerId: string;
    status: 'active' | 'idle' | 'offline';
    lastSeen: Date;
    jobsProcessed: number;
    currentJob?: string;
}

export interface QueueMetrics {
    pendingJobs: number;
    processingJobs: number;
    completedToday: number;
    failedToday: number;
    averageProcessingTime: number;
}

export interface SystemMetrics {
    activeRuns: number;
    activeWorkers: number;
    queueDepth: number;
    throughput: number; // jobs per minute
}

export class MetricsCollector {
    private redis: Redis;
    private isRunning: boolean = false;
    private metrics: SystemMetrics = {
        activeRuns: 0,
        activeWorkers: 0,
        queueDepth: 0,
        throughput: 0
    };

    constructor() {
        this.redis = new Redis(process.env.REDIS_URL || 'redis://localhost:6379');
    }

    async start(): Promise<void> {
        if (this.isRunning) return;
        this.isRunning = true;

        console.log('[MetricsCollector] Starting metrics collection...');

        // Collect metrics every 30 seconds
        this.collectLoop();
    }

    private async collectLoop(): Promise<void> {
        while (this.isRunning) {
            try {
                await this.collectMetrics();
            } catch (err) {
                console.error('[MetricsCollector] Error collecting metrics:', err);
            }
            await this.sleep(30000);
        }
    }

    private async collectMetrics(): Promise<void> {
        // Count active runs
        const runKeys = await this.redis.keys('runs:*:progress');
        let activeRuns = 0;
        for (const key of runKeys) {
            const status = await this.redis.hget(key, 'status');
            if (status === 'running' || status === 'pending') {
                activeRuns++;
            }
        }

        // Count pending jobs in stream
        const streamInfo = await this.redis.xinfo('STREAM', 'jobs:pending').catch(() => null);
        const queueDepth = streamInfo ? (streamInfo as any)[1] : 0;

        // Get consumer group info for active workers
        let activeWorkers = 0;
        try {
            const groups = await this.redis.xinfo('GROUPS', 'jobs:pending') as any[];
            if (groups && groups.length > 0) {
                const consumers = await this.redis.xinfo('CONSUMERS', 'jobs:pending', 'execution-workers') as any[];
                activeWorkers = consumers?.length || 0;
            }
        } catch (err) {
            // Group might not exist yet
        }

        // Calculate throughput from results stream
        const now = Date.now();
        const oneMinuteAgo = now - 60000;
        const results = await this.redis.xrange(
            'jobs:results', 
            oneMinuteAgo.toString(), 
            now.toString()
        );
        const throughput = results?.length || 0;

        this.metrics = {
            activeRuns,
            activeWorkers,
            queueDepth,
            throughput
        };

        // Store metrics in Redis for dashboard access
        await this.redis.hset('controller:metrics', {
            activeRuns: activeRuns.toString(),
            activeWorkers: activeWorkers.toString(),
            queueDepth: queueDepth.toString(),
            throughput: throughput.toString(),
            updatedAt: new Date().toISOString()
        });
    }

    /**
     * Get current metrics
     */
    getMetrics(): SystemMetrics {
        return { ...this.metrics };
    }

    /**
     * Get detailed worker status
     */
    async getWorkerStatus(): Promise<WorkerStatus[]> {
        const workers: WorkerStatus[] = [];
        
        try {
            const consumers = await this.redis.xinfo('CONSUMERS', 'jobs:pending', 'execution-workers') as any[];
            
            if (consumers) {
                for (const consumer of consumers) {
                    const [, name, , pending, , idle] = consumer;
                    workers.push({
                        workerId: name,
                        status: pending > 0 ? 'active' : (idle < 60000 ? 'idle' : 'offline'),
                        lastSeen: new Date(Date.now() - idle),
                        jobsProcessed: 0, // Would need separate tracking
                        currentJob: pending > 0 ? 'processing' : undefined
                    });
                }
            }
        } catch (err) {
            // Consumer group might not exist
        }

        return workers;
    }

    /**
     * Get queue metrics
     */
    async getQueueMetrics(): Promise<QueueMetrics> {
        let pendingJobs = 0;
        let processingJobs = 0;

        try {
            // Get stream length (pending)
            const streamLen = await this.redis.xlen('jobs:pending');
            
            // Get pending entries in consumer group
            const pendingInfo = await this.redis.xpending('jobs:pending', 'execution-workers') as any[];
            if (pendingInfo && pendingInfo[0]) {
                processingJobs = pendingInfo[0]; // Number of pending entries
                pendingJobs = streamLen - processingJobs;
            } else {
                pendingJobs = streamLen;
            }
        } catch (err) {
            // Stream might not exist
        }

        // Count completed/failed today
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        
        let completedToday = 0;
        let failedToday = 0;

        try {
            const results = await this.redis.xrange(
                'jobs:results',
                today.getTime().toString(),
                '+',
                'COUNT', 1000
            );
            
            for (const [, fields] of results) {
                const result = JSON.parse(fields[1]);
                if (result.status === 'passed') {
                    completedToday++;
                } else {
                    failedToday++;
                }
            }
        } catch (err) {
            // Results stream might not exist
        }

        return {
            pendingJobs,
            processingJobs,
            completedToday,
            failedToday,
            averageProcessingTime: 0 // Would need tracking
        };
    }

    /**
     * Get historical metrics for dashboard charts
     */
    async getHistoricalMetrics(hours: number = 24): Promise<any[]> {
        // This would require storing time-series data
        // For now, return empty array
        return [];
    }

    private sleep(ms: number): Promise<void> {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    async stop(): Promise<void> {
        this.isRunning = false;
        await this.redis.quit();
        console.log('[MetricsCollector] Metrics collection stopped');
    }
}
