import { chromium, Browser, BrowserContext, Page } from 'playwright';
import * as Minio from 'minio';
import * as fs from 'fs';
import * as path from 'path';

const minioClient = new Minio.Client({
    endPoint: process.env.MINIO_ENDPOINT || 'localhost',
    port: parseInt(process.env.MINIO_PORT || '9000'),
    useSSL: false,
    accessKey: process.env.MINIO_ACCESS_KEY || 'minioadmin',
    secretKey: process.env.MINIO_SECRET_KEY || 'minioadmin'
});

const BUCKET_NAME = process.env.MINIO_BUCKET_NAME || 'test-artifacts';

export class PlaywrightRunner {
    private browser: Browser | null = null;

    async start() {
        this.browser = await chromium.launch({ headless: true });
    }

    async stop() {
        if (this.browser) {
            await this.browser.close();
        }
    }

    async runTest(runId: number): Promise<any> {
        if (!this.browser) await this.start();

        const artifactsDir = `/tmp/artifacts/${runId}`;
        fs.mkdirSync(artifactsDir, { recursive: true });

        const context = await this.browser!.newContext({
            recordVideo: { dir: artifactsDir, size: { width: 1280, height: 720 } }
        });

        const tracePath = path.join(artifactsDir, 'trace.zip');
        await context.tracing.start({ screenshots: true, snapshots: true, sources: true });

        const page = await context.newPage();
        const startTime = Date.now();
        let status = 'passed';
        let error = null;

        try {
            console.log(`Running test for runId: ${runId}`);
            await page.goto('https://www.thehindu.com');
            // Simulate interaction
            // await page.click('text=More information...');
        } catch (e: any) {
            status = 'failed';
            error = e.message;
            console.error(`Test failed: ${e}`);
        } finally {
            const duration = Date.now() - startTime;
            await context.tracing.stop({ path: tracePath });
            await context.close(); // Saves video

            // Upload artifacts
            const traceKey = `runs/${runId}/trace.zip`;
            await minioClient.fPutObject(BUCKET_NAME, traceKey, tracePath);

            let videoKey = null;
            const files = fs.readdirSync(artifactsDir);
            const videoFile = files.find(f => f.endsWith('.webm'));
            if (videoFile) {
                const videoPath = path.join(artifactsDir, videoFile);
                videoKey = `runs/${runId}/video.webm`;
                await minioClient.fPutObject(BUCKET_NAME, videoKey, videoPath);
            }

            // Cleanup
            fs.rmSync(artifactsDir, { recursive: true, force: true });

            return {
                status,
                duration_ms: duration,
                error,
                trace: traceKey,
                video: videoKey
            };
        }
    }
}
