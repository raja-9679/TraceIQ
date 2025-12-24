import express from 'express';
import bodyParser from 'body-parser';
import { PlaywrightRunner } from './runner';

const app = express();
const port = process.env.PORT || 3000;
const runner = new PlaywrightRunner();

app.use(bodyParser.json());

app.post('/run', async (req, res) => {
    const { runId, testCases, browser, globalSettings, device } = req.body;
    console.log(`Received run request for runId: ${runId}`);
    console.log(`Test Cases received: ${JSON.stringify(testCases)}`);
    console.log(`Browser: ${browser}`);
    console.log(`Device: ${device || 'Desktop'}`);
    console.log(`Global Settings: ${JSON.stringify(globalSettings)}`);
    if (!runId) {
        return res.status(400).json({ error: 'runId is required' });
    }

    try {
        const result = await runner.runTest(runId, testCases, browser, globalSettings, device);
        res.json(result);
    } catch (e: any) {
        res.status(500).json({ error: e.message });
    }
});

app.listen(port, () => {
    console.log(`Execution Engine listening at http://localhost:${port}`);
});
