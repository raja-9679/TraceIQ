import Redis from 'ioredis';
import { v4 as uuidv4 } from 'uuid';

// Types for job queue system
// Single test case structure
export interface TestCase {
    id: number;
    name: string;
    steps: any[];
    // Auth sessions: a passing run of an auth-setup case captures the
    // context's storageState; use_auth_session=false opts a case out of
    // starting from the stored state (e.g. login-flow tests).
    is_auth_setup?: boolean;
    use_auth_session?: boolean;
    // Data-driven expansion: the dataset row for this execution and its
    // index (the name is already suffixed "[row N]" by the backend).
    data_row?: Record<string, any>;
    row_index?: number;
}

// Job can be either:
// 1. Single test case (original SEPARATE mode): has test_case_id and test_case
// 2. Multi-test continuous job (hybrid mode): has execution_mode='continuous' and test_cases[]
export interface TestJob {
    job_id: string;
    run_id: number;
    
    // For single test case jobs
    test_case_id?: number;
    test_case?: TestCase;
    
    // For multi-test continuous jobs (sub-suite execution)
    execution_mode?: 'continuous' | 'separate';
    unit_type?: 'sub_suite' | 'test_case';
    unit_id?: number;
    unit_name?: string;
    test_cases?: TestCase[];
    
    browser: string;
    device?: string;
    settings: {
        headers: Record<string, string>;
        params: Record<string, string>;
        allowed_domains: any[];
        domain_settings: Record<string, any>;
        // Playwright storageState injected by the backend when the project
        // has a fresh AuthSession.
        storage_state?: any;
        // Environment ({{env.X}}, base_url for relative gotos) and decrypted
        // project secrets ({{secret.X}}), dispatched by the backend.
        environment?: { name: string; base_url?: string; variables: Record<string, any> };
        secrets?: Record<string, string>;
        // Per-test retry policy dispatched by the backend from suite settings.
        // When auto_retry is true a failed/errored test case is re-run up to
        // max_retries times with exponential backoff.
        auto_retry?: boolean;
        max_retries?: number;
        retry_backoff_ms?: number;
    };
    created_at: string;
    retry_count?: number;
}

// Result for a single test case within a job
export interface TestCaseResult {
    test_case_id: number;
    test_name: string;
    status: 'passed' | 'failed' | 'error';
    duration_ms: number;
    error?: string;
    response_data?: {
        status?: number;
        headers?: Record<string, string>;
        body?: string;
    };
    video?: string;  // Per-case video for continuous jobs
    network_events?: any[];  // Network events captured during this test case
}

// Job result - can contain single or multiple test results
export interface JobResult {
    job_id: string;
    run_id: number;
    
    // For single test case jobs (backward compatibility)
    test_case_id?: number;
    test_name?: string;
    status: 'passed' | 'failed' | 'error';
    duration_ms: number;
    error?: string;

    // Playwright storageState captured by a passing auth-setup case; the
    // backend persists it as the project's AuthSession. auth_case_id names
    // the capturing case when the job ran multiple cases (continuous mode).
    auth_state?: any;
    auth_case_id?: number;

    // Reactive selector-heal suggestions gathered on selector failures; the
    // backend persists them as pending SelectorHealProposal rows.
    heal_suggestions?: Array<{
        test_case_id: number;
        step_id: string;
        old_selector: string;
        new_selector: string;
        matches: number;
        intent?: string | null;
    }>;

    // Artifacts at job level
    artifacts: {
        video?: string;
        trace?: string;
        screenshots: string[];
    };
    
    // For single test jobs
    response_data?: {
        status?: number;
        headers?: Record<string, string>;
        body?: string;
    };
    
    // For multi-test continuous jobs
    test_results?: TestCaseResult[];

    network_events: any[];
    // Number of RETRIES performed for this test (0 = passed first attempt).
    retry_count?: number;
    completed_at: string;
}

export interface RunProgress {
    total: number;
    completed: number;
    passed: number;
    failed: number;
    status: 'pending' | 'running' | 'completed';
}

// Configuration
const JOBS_STREAM = process.env.REDIS_JOBS_STREAM || 'jobs:pending';
const RESULTS_STREAM = process.env.REDIS_RESULTS_STREAM || 'jobs:results';
const CONSUMER_GROUP = process.env.REDIS_CONSUMER_GROUP || 'execution-workers';
const JOB_TIMEOUT_MS = parseInt(process.env.WORKER_JOB_TIMEOUT || '300000'); // 5 minutes
const CLAIM_IDLE_TIME_MS = parseInt(process.env.JOB_CLAIM_IDLE_TIME || '60000'); // 1 minute

export class JobQueue {
    private redis: Redis;
    private workerId: string;
    
    constructor(redisUrl?: string) {
        this.redis = new Redis(redisUrl || process.env.REDIS_URL || 'redis://localhost:6379');
        this.workerId = `worker-${uuidv4().slice(0, 8)}`;
        
        this.redis.on('connect', () => console.log(`[JobQueue] Connected to Redis as ${this.workerId}`));
        this.redis.on('error', (err) => console.error('[JobQueue] Redis error:', err));
    }

    /**
     * Initialize consumer group (idempotent)
     */
    async initialize(): Promise<void> {
        try {
            // Create stream and consumer group if they don't exist
            await this.redis.xgroup('CREATE', JOBS_STREAM, CONSUMER_GROUP, '0', 'MKSTREAM');
            console.log(`[JobQueue] Created consumer group: ${CONSUMER_GROUP}`);
        } catch (err: any) {
            if (err.message.includes('BUSYGROUP')) {
                console.log(`[JobQueue] Consumer group already exists: ${CONSUMER_GROUP}`);
            } else {
                throw err;
            }
        }

        try {
            await this.redis.xgroup('CREATE', RESULTS_STREAM, 'result-processors', '0', 'MKSTREAM');
            console.log(`[JobQueue] Created results consumer group`);
        } catch (err: any) {
            if (!err.message.includes('BUSYGROUP')) {
                throw err;
            }
        }
    }

    /**
     * Add a job to the pending queue
     */
    async enqueueJob(job: Omit<TestJob, 'job_id' | 'created_at'>): Promise<string> {
        const jobId = uuidv4();
        const fullJob: TestJob = {
            ...job,
            job_id: jobId,
            created_at: new Date().toISOString(),
            retry_count: 0
        };

        await this.redis.xadd(
            JOBS_STREAM,
            '*',
            'job_id', jobId,
            'run_id', job.run_id.toString(),
            'payload', JSON.stringify(fullJob)
        );

        // Track job in run's job set
        await this.redis.sadd(`runs:${job.run_id}:job_ids`, jobId);
        
        console.log(`[JobQueue] Enqueued job ${jobId} for run ${job.run_id}`);
        return jobId;
    }

    /**
     * Enqueue multiple jobs for a run (batch operation)
     */
    async enqueueJobBatch(jobs: Omit<TestJob, 'job_id' | 'created_at'>[]): Promise<string[]> {
        const pipeline = this.redis.pipeline();
        const jobIds: string[] = [];

        for (const job of jobs) {
            const jobId = uuidv4();
            jobIds.push(jobId);
            
            const fullJob: TestJob = {
                ...job,
                job_id: jobId,
                created_at: new Date().toISOString(),
                retry_count: 0
            };

            pipeline.xadd(
                JOBS_STREAM,
                '*',
                'job_id', jobId,
                'run_id', job.run_id.toString(),
                'payload', JSON.stringify(fullJob)
            );
            pipeline.sadd(`runs:${job.run_id}:job_ids`, jobId);
        }

        // Initialize run progress tracking
        if (jobs.length > 0) {
            const runId = jobs[0].run_id;
            pipeline.hset(`runs:${runId}:progress`, {
                total: jobs.length,
                completed: 0,
                passed: 0,
                failed: 0,
                status: 'pending'
            });
        }

        await pipeline.exec();
        console.log(`[JobQueue] Enqueued ${jobs.length} jobs`);
        return jobIds;
    }

    /**
     * Claim and return the next available job
     */
    async claimJob(): Promise<{ streamId: string; job: TestJob } | null> {
        // First, try to claim any abandoned jobs (from crashed workers)
        const abandoned = await this.claimAbandonedJob();
        if (abandoned) return abandoned;

        // Read new job from stream
        const results = await this.redis.xreadgroup(
            'GROUP', CONSUMER_GROUP, this.workerId,
            'COUNT', 1,
            'BLOCK', 5000, // Block for 5 seconds
            'STREAMS', JOBS_STREAM,
            '>'
        ) as any;

        if (!results || results.length === 0) {
            return null;
        }

        const [[, messages]] = results;
        if (!messages || messages.length === 0) {
            return null;
        }

        const [streamId, fields] = messages[0];
        const payload = this.parseStreamFields(fields);
        
        if (!payload.payload) {
            console.error(`[JobQueue] Invalid job format at ${streamId}`);
            await this.redis.xack(JOBS_STREAM, CONSUMER_GROUP, streamId);
            return null;
        }

        const job = JSON.parse(payload.payload) as TestJob;
        console.log(`[JobQueue] Claimed job ${job.job_id} (run ${job.run_id})`);
        
        // Update run status to running
        await this.redis.hset(`runs:${job.run_id}:progress`, 'status', 'running');

        // Record the first time any worker picks up a job for this run.
        // HSETNX is idempotent: only writes if the field doesn't exist yet,
        // so parallel workers racing to pick up jobs won't overwrite it.
        await this.redis.hsetnx(
            `runs:${job.run_id}:progress`,
            'worker_started_at',
            new Date().toISOString()
        );
        
        return { streamId, job };
    }

    /**
     * Try to claim an abandoned job from crashed worker
     */
    private async claimAbandonedJob(): Promise<{ streamId: string; job: TestJob } | null> {
        try {
            const claimed = await this.redis.xclaim(
                JOBS_STREAM,
                CONSUMER_GROUP,
                this.workerId,
                CLAIM_IDLE_TIME_MS,
                '0-0', // Claim from beginning
                'COUNT', 1
            ) as any;

            if (!claimed || claimed.length === 0) {
                return null;
            }

            const [streamId, fields] = claimed[0];
            const payload = this.parseStreamFields(fields);
            const job = JSON.parse(payload.payload) as TestJob;

            // Atomically increment and read retry count from Redis so it
            // survives worker crashes (in-memory increment is lost on crash).
            const retryCount = await this.redis.hincrby('jobs:retries', job.job_id, 1);
            job.retry_count = retryCount;

            console.log(`[JobQueue] Claimed abandoned job ${job.job_id} (retry #${job.retry_count})`);

            // Check if max retries exceeded
            if (job.retry_count > 3) {
                console.log(`[JobQueue] Job ${job.job_id} exceeded max retries, moving to dead letter`);
                await this.moveToDeadLetter(streamId, job, 'Max retries exceeded');
                return null;
            }

            return { streamId, job };
        } catch (err) {
            // No abandoned jobs
            return null;
        }
    }

    /**
     * Mark job as completed and publish result
     */
    async completeJob(streamId: string, result: JobResult): Promise<void> {
        const pipeline = this.redis.pipeline();

        // Acknowledge the job
        pipeline.xack(JOBS_STREAM, CONSUMER_GROUP, streamId);

        // Clean up per-job retry counter (no longer needed once complete)
        pipeline.hdel('jobs:retries', result.job_id);

        // Publish result
        pipeline.xadd(
            RESULTS_STREAM,
            '*',
            'job_id', result.job_id,
            'run_id', result.run_id.toString(),
            'result', JSON.stringify(result)
        );

        // Update run progress
        // For multi-test continuous jobs, count each test result separately
        const progressKey = `runs:${result.run_id}:progress`;
        
        if (result.test_results && result.test_results.length > 0) {
            // Multi-test continuous job - count each test individually
            const passedCount = result.test_results.filter(r => r.status === 'passed').length;
            const failedCount = result.test_results.filter(r => r.status !== 'passed').length;
            
            pipeline.hincrby(progressKey, 'completed', result.test_results.length);
            pipeline.hincrby(progressKey, 'passed', passedCount);
            pipeline.hincrby(progressKey, 'failed', failedCount);
            
            console.log(`[JobQueue] Continuous job ${result.job_id} completed: ${passedCount} passed, ${failedCount} failed`);
        } else {
            // Single test job - original behavior
            pipeline.hincrby(progressKey, 'completed', 1);
            if (result.status === 'passed') {
                pipeline.hincrby(progressKey, 'passed', 1);
            } else {
                pipeline.hincrby(progressKey, 'failed', 1);
            }
        }

        await pipeline.exec();

        // Check if run is complete
        await this.checkRunCompletion(result.run_id);
        
        console.log(`[JobQueue] Completed job ${result.job_id} (${result.status})`);
    }

    /**
     * Check if all jobs for a run are complete
     */
    private async checkRunCompletion(runId: number): Promise<void> {
        const progress = await this.redis.hgetall(`runs:${runId}:progress`) as unknown as RunProgress;
        
        if (progress && Number(progress.completed) >= Number(progress.total)) {
            // Mark run as completed
            await this.redis.hset(`runs:${runId}:progress`, 'status', 'completed');
            
            // Publish run completion event
            await this.redis.publish(`run:${runId}:complete`, JSON.stringify({
                run_id: runId,
                total: progress.total,
                passed: progress.passed,
                failed: progress.failed
            }));
            
            console.log(`[JobQueue] Run ${runId} completed: ${progress.passed} passed, ${progress.failed} failed`);
        }
    }

    /**
     * Check the dead-letter queue length and log a warning if it is non-empty.
     * Call this periodically from the worker loop so ops teams see the alert.
     */
    async checkDeadLetterQueue(): Promise<void> {
        try {
            const dlqLength = await this.redis.xlen('jobs:dead-letter');
            if (dlqLength > 0) {
                console.error(`[JobQueue] ALERT: dead-letter queue has ${dlqLength} unprocessed job(s). Investigate jobs:dead-letter stream.`);
            }
        } catch (err) {
            // Non-fatal; stream may not exist yet
        }
    }

    /**
     * Move failed job to dead letter queue
     */
    private async moveToDeadLetter(streamId: string, job: TestJob, error: string): Promise<void> {
        const pipeline = this.redis.pipeline();

        pipeline.xack(JOBS_STREAM, CONSUMER_GROUP, streamId);
        pipeline.hdel('jobs:retries', job.job_id);
        pipeline.xadd(
            'jobs:dead-letter',
            '*',
            'job_id', job.job_id,
            'run_id', job.run_id.toString(),
            'error', error,
            'payload', JSON.stringify(job)
        );

        // Mark as failed in progress
        pipeline.hincrby(`runs:${job.run_id}:progress`, 'completed', 1);
        pipeline.hincrby(`runs:${job.run_id}:progress`, 'failed', 1);

        await pipeline.exec();
        await this.checkRunCompletion(job.run_id);
    }

    /**
     * Ack a discovery (Mode-2 crawl) job and publish its result under a
     * dedicated key the backend polls. Discovery jobs are not test runs, so
     * they never touch run-progress counters.
     */
    async completeDiscoveryJob(streamId: string, jobId: string, discoveryId: string, result: any): Promise<void> {
        const pipeline = this.redis.pipeline();
        pipeline.xack(JOBS_STREAM, CONSUMER_GROUP, streamId);
        pipeline.hdel('jobs:retries', jobId);
        // 10-minute TTL — the backend long-poll picks it up well within that.
        pipeline.set(`discovery:result:${discoveryId}`, JSON.stringify(result), 'EX', 600);
        await pipeline.exec();
    }

    /**
     * Get current progress for a run
     */
    async getRunProgress(runId: number): Promise<RunProgress | null> {
        const progress = await this.redis.hgetall(`runs:${runId}:progress`);
        if (!progress || Object.keys(progress).length === 0) {
            return null;
        }
        return {
            total: Number(progress.total),
            completed: Number(progress.completed),
            passed: Number(progress.passed),
            failed: Number(progress.failed),
            status: progress.status as RunProgress['status']
        };
    }

    /**
     * Get queue statistics
     */
    async getQueueStats(): Promise<{
        pending: number;
        processing: number;
        results: number;
    }> {
        const [pending, info, results] = await Promise.all([
            this.redis.xlen(JOBS_STREAM),
            this.redis.xinfo('GROUPS', JOBS_STREAM).catch(() => []),
            this.redis.xlen(RESULTS_STREAM)
        ]);

        let processing = 0;
        if (Array.isArray(info) && info.length > 0) {
            // Find our consumer group's pending count
            for (const group of info) {
                if (Array.isArray(group)) {
                    const idx = group.indexOf('pending');
                    if (idx !== -1) {
                        processing += Number(group[idx + 1]);
                    }
                }
            }
        }

        return { pending, processing, results };
    }

    /**
     * Helper to parse Redis stream field array into object
     */
    private parseStreamFields(fields: string[]): Record<string, string> {
        const result: Record<string, string> = {};
        for (let i = 0; i < fields.length; i += 2) {
            result[fields[i]] = fields[i + 1];
        }
        return result;
    }

    /**
     * Graceful shutdown
     */
    async shutdown(): Promise<void> {
        console.log(`[JobQueue] Shutting down worker ${this.workerId}`);
        await this.redis.quit();
    }
}

// Singleton instance
let jobQueueInstance: JobQueue | null = null;

export function getJobQueue(): JobQueue {
    if (!jobQueueInstance) {
        jobQueueInstance = new JobQueue();
    }
    return jobQueueInstance;
}
