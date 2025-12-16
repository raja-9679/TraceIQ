import express from 'express';
import bodyParser from 'body-parser';
import { PlaywrightRunner } from './runner';

const app = express();
const port = process.env.PORT || 3000;
const runner = new PlaywrightRunner();

app.use(bodyParser.json());

app.post('/run', async (req, res) => {
    const { runId } = req.body;
    if (!runId) {
        return res.status(400).json({ error: 'runId is required' });
    }

    // Run async, don't block? Or block for now since Celery waits?
    // Celery worker calls this, so we can block or return pending.
    // Let's block for simplicity in this MVP, or return immediately and use webhook?
    // The Python worker is synchronous-ish (it waits). Let's wait.

    try {
        const result = await runner.runTest(runId);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

app.listen(port, () => {
    console.log(`Execution Engine listening at http://localhost:${port}`);
});
