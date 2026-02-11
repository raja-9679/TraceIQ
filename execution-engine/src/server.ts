/**
 * Execution Engine Server
 * 
 * Now serves as:
 * 1. Controller/Coordinator for distributed execution
 * 2. Metrics and monitoring API
 * 3. Optional AI analysis (results sent to Backend)
 * 4. Legacy execution endpoint (DEPRECATED - only for fallback)
 * 
 * PRIMARY ARCHITECTURE:
 * - All test cases are dispatched to Redis queue by backend
 * - Execution workers pull jobs one at a time from queue
 * - This ensures true parallel execution based on worker count
 * 
 * NOTE: Notifications (email, Slack) are handled by Backend
 * because it has access to user preferences and DB.
 */

import express from 'express';
import bodyParser from 'body-parser';
import { PlaywrightRunner } from './runner';
import { ExecutionController } from './controller';
import { RunMonitor } from './controller/run-monitor';
import { MetricsCollector } from './controller/metrics-collector';
import { AIAnalyzer } from './controller/ai-analyzer';

const app = express();
const port = process.env.PORT || 3000;
const runner = new PlaywrightRunner();

// Concurrency control for legacy /run endpoint
const MAX_CONCURRENT_RUNS = parseInt(process.env.MAX_CONCURRENT_RUNS || '3', 10);
let activeRuns = 0;
const pendingQueue: Array<{ resolve: () => void; reject: (err: Error) => void }> = [];

async function acquireRunSlot(): Promise<void> {
    if (activeRuns < MAX_CONCURRENT_RUNS) {
        activeRuns++;
        return Promise.resolve();
    }
    // Queue the request
    return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
            const idx = pendingQueue.findIndex(p => p.resolve === resolve);
            if (idx !== -1) pendingQueue.splice(idx, 1);
            reject(new Error('Queue timeout: too many pending runs'));
        }, 300000); // 5 minute queue timeout
        
        pendingQueue.push({
            resolve: () => {
                clearTimeout(timeout);
                activeRuns++;
                resolve();
            },
            reject
        });
    });
}

function releaseRunSlot(): void {
    activeRuns--;
    if (pendingQueue.length > 0 && activeRuns < MAX_CONCURRENT_RUNS) {
        const next = pendingQueue.shift();
        if (next) next.resolve();
    }
}

// Initialize controller components
const controller = new ExecutionController();
const runMonitor = new RunMonitor();
const metricsCollector = new MetricsCollector();

// AI Analyzer is optional
const aiEnabled = process.env.AI_ANALYSIS_ENABLED === 'true';
const aiAnalyzer = aiEnabled ? new AIAnalyzer() : null;

app.use(bodyParser.json());

// ============================================
// CONTROLLER ENDPOINTS
// ============================================

/**
 * Get system metrics and health status
 */
app.get('/api/metrics', async (req, res) => {
    try {
        const metrics = metricsCollector.getMetrics();
        const queueMetrics = await metricsCollector.getQueueMetrics();
        const workers = await metricsCollector.getWorkerStatus();

        res.json({
            system: metrics,
            queue: queueMetrics,
            workers,
            timestamp: new Date().toISOString()
        });
    } catch (err: any) {
        res.status(500).json({ error: err.message });
    }
});

/**
 * Get worker status
 */
app.get('/api/workers', async (req, res) => {
    try {
        const workers = await metricsCollector.getWorkerStatus();
        res.json({ workers });
    } catch (err: any) {
        res.status(500).json({ error: err.message });
    }
});

/**
 * Get active runs
 */
app.get('/api/runs/active', async (req, res) => {
    try {
        const activeRuns = await runMonitor.getActiveRuns();
        res.json({ runs: activeRuns });
    } catch (err: any) {
        res.status(500).json({ error: err.message });
    }
});

/**
 * Get run progress
 */
app.get('/api/runs/:runId/progress', async (req, res) => {
    try {
        const runId = parseInt(req.params.runId);
        const progress = await runMonitor.getRunProgress(runId);
        res.json({ runId, progress });
    } catch (err: any) {
        res.status(500).json({ error: err.message });
    }
});

/**
 * AI Analysis endpoint - analyze failures for a run (OPTIONAL)
 */
app.post('/api/runs/:runId/analyze', async (req, res) => {
    if (!aiEnabled || !aiAnalyzer) {
        return res.status(400).json({ 
            error: 'AI analysis is disabled',
            hint: 'Set AI_ANALYSIS_ENABLED=true to enable'
        });
    }

    try {
        const runId = parseInt(req.params.runId);
        const { results } = req.body;

        if (!results || !Array.isArray(results)) {
            return res.status(400).json({ error: 'results array is required' });
        }

        const analysis = await aiAnalyzer.analyzeFailures(runId, results);
        res.json({ runId, analysis });
    } catch (err: any) {
        res.status(500).json({ error: err.message });
    }
});

/**
 * Health check endpoint
 */
app.get('/health', (req, res) => {
    res.json({ 
        status: 'healthy',
        service: 'execution-controller',
        features: {
            aiAnalysis: aiEnabled,
            metricsCollection: true,
            runMonitoring: true
        },
        uptime: process.uptime(),
        timestamp: new Date().toISOString()
    });
});

// ============================================
// LEGACY EXECUTION ENDPOINT (CONTINUOUS MODE)
// With concurrency control to prevent resource exhaustion
// ============================================

app.post('/run', async (req, res) => {
    const { runId, testCases, browser, globalSettings, device, executionMode, callbackUrl, webhookSecret } = req.body;
    console.log(`Received run request for runId: ${runId}`);
    console.log(`Test Cases count: ${testCases?.length || 0}`);
    console.log(`Browser: ${browser}`);
    console.log(`Device: ${device || 'Desktop'}`);
    console.log(`Execution Mode: ${executionMode}`);
    console.log(`Active runs: ${activeRuns}/${MAX_CONCURRENT_RUNS}, Queue: ${pendingQueue.length}`);
    
    if (!runId) {
        return res.status(400).json({ error: 'runId is required' });
    }

    try {
        // Try to acquire a run slot (may queue if at capacity)
        try {
            await acquireRunSlot();
            console.log(`[Concurrency] Run ${runId} acquired slot (active: ${activeRuns}/${MAX_CONCURRENT_RUNS})`);
        } catch (queueErr: any) {
            console.error(`[Concurrency] Run ${runId} rejected: ${queueErr.message}`);
            return res.status(503).json({ 
                error: 'Service overloaded', 
                message: queueErr.message,
                activeRuns,
                queueSize: pendingQueue.length
            });
        }

        // Run in background with slot release
        runner.runTest(runId, testCases, browser, globalSettings, device, executionMode, callbackUrl, webhookSecret)
            .catch(err => console.error(`Error in async test run ${runId}:`, err))
            .finally(() => {
                releaseRunSlot();
                console.log(`[Concurrency] Run ${runId} released slot (active: ${activeRuns}/${MAX_CONCURRENT_RUNS})`);
            });

        res.status(202).json({ status: 'accepted', message: 'Test execution started', runId });
    } catch (e: any) {
        releaseRunSlot();
        res.status(500).json({ error: e.message });
    }
});

// ============================================
// START SERVER AND CONTROLLER
// ============================================

async function startServer() {
    try {
        // Start controller services
        await controller.start();
        await runMonitor.start();
        await metricsCollector.start();

        app.listen(port, () => {
            console.log(`\n========================================`);
            console.log(`  TraceIQ Execution Controller`);
            console.log(`  Listening at http://localhost:${port}`);
            console.log(`========================================`);
            console.log(`\nEndpoints:`);
            console.log(`  GET  /health              - Health check`);
            console.log(`  GET  /api/metrics         - System metrics`);
            console.log(`  GET  /api/workers         - Worker status`);
            console.log(`  GET  /api/runs/active     - Active runs`);
            console.log(`  GET  /api/runs/:id/progress - Run progress`);
            console.log(`  POST /api/runs/:id/analyze  - AI analysis`);
            console.log(`  POST /run                 - Legacy execution\n`);
        });
    } catch (err) {
        console.error('Failed to start server:', err);
        process.exit(1);
    }
}

// Handle graceful shutdown
process.on('SIGTERM', async () => {
    console.log('Received SIGTERM, shutting down...');
    await controller.stop();
    await runMonitor.stop();
    await metricsCollector.stop();
    process.exit(0);
});

process.on('SIGINT', async () => {
    console.log('Received SIGINT, shutting down...');
    await controller.stop();
    await runMonitor.stop();
    await metricsCollector.stop();
    process.exit(0);
});

startServer();
