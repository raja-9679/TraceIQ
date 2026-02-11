/**
 * Notification Service - Sends notifications on test completion
 * 
 * Channels:
 * 1. Email (SMTP)
 * 2. Slack
 * 3. Microsoft Teams
 * 4. Custom Webhooks
 */

// Nodemailer is optional - will be loaded dynamically if available
let nodemailer: any = null;
try {
    nodemailer = require('nodemailer');
} catch (e) {
    console.log('[NotificationService] nodemailer not installed, email notifications disabled');
}

export interface NotificationConfig {
    email?: EmailConfig;
    slack?: SlackConfig;
    teams?: TeamsConfig;
    webhook?: WebhookConfig;
}

export interface EmailConfig {
    enabled: boolean;
    host: string;
    port: number;
    secure: boolean;
    user: string;
    pass: string;
    from: string;
    recipients: string[];
}

export interface SlackConfig {
    enabled: boolean;
    webhookUrl: string;
    channel?: string;
}

export interface TeamsConfig {
    enabled: boolean;
    webhookUrl: string;
}

export interface WebhookConfig {
    enabled: boolean;
    url: string;
    headers?: Record<string, string>;
}

export class NotificationService {
    private emailTransporter: any = null;
    private config: NotificationConfig;

    constructor() {
        this.config = this.loadConfig();
        this.initializeEmail();
    }

    private loadConfig(): NotificationConfig {
        return {
            email: {
                enabled: process.env.EMAIL_ENABLED === 'true',
                host: process.env.SMTP_HOST || 'smtp.gmail.com',
                port: parseInt(process.env.SMTP_PORT || '587'),
                secure: process.env.SMTP_SECURE === 'true',
                user: process.env.SMTP_USER || '',
                pass: process.env.SMTP_PASS || '',
                from: process.env.EMAIL_FROM || 'TraceIQ <noreply@traceiq.io>',
                recipients: (process.env.EMAIL_RECIPIENTS || '').split(',').filter(Boolean)
            },
            slack: {
                enabled: process.env.SLACK_ENABLED === 'true',
                webhookUrl: process.env.SLACK_WEBHOOK_URL || '',
                channel: process.env.SLACK_CHANNEL
            },
            teams: {
                enabled: process.env.TEAMS_ENABLED === 'true',
                webhookUrl: process.env.TEAMS_WEBHOOK_URL || ''
            },
            webhook: {
                enabled: process.env.CUSTOM_WEBHOOK_ENABLED === 'true',
                url: process.env.CUSTOM_WEBHOOK_URL || '',
                headers: process.env.CUSTOM_WEBHOOK_HEADERS 
                    ? JSON.parse(process.env.CUSTOM_WEBHOOK_HEADERS) 
                    : {}
            }
        };
    }

    private initializeEmail(): void {
        if (!nodemailer) {
            console.log('[NotificationService] nodemailer not available, skipping email setup');
            return;
        }
        
        if (this.config.email?.enabled && this.config.email.user) {
            try {
                this.emailTransporter = nodemailer.createTransport({
                    host: this.config.email.host,
                    port: this.config.email.port,
                    secure: this.config.email.secure,
                    auth: {
                        user: this.config.email.user,
                        pass: this.config.email.pass
                    }
                });
                console.log('[NotificationService] Email transport initialized');
            } catch (err) {
                console.error('[NotificationService] Failed to initialize email:', err);
            }
        }
    }

    /**
     * Send notification for completed test run
     */
    async notifyRunCompletion(runId: number, results: any): Promise<void> {
        console.log(`[NotificationService] Sending notifications for run ${runId}`);

        const notifications: Promise<void>[] = [];

        // Only notify on failures by default, or based on config
        const shouldNotify = results.summary.failed > 0 || 
                           process.env.NOTIFY_ON_SUCCESS === 'true';

        if (!shouldNotify) {
            console.log(`[NotificationService] Skipping notification for successful run ${runId}`);
            return;
        }

        if (this.config.email?.enabled) {
            notifications.push(this.sendEmail(runId, results));
        }

        if (this.config.slack?.enabled) {
            notifications.push(this.sendSlack(runId, results));
        }

        if (this.config.teams?.enabled) {
            notifications.push(this.sendTeams(runId, results));
        }

        if (this.config.webhook?.enabled) {
            notifications.push(this.sendWebhook(runId, results));
        }

        await Promise.allSettled(notifications);
    }

    /**
     * Send email notification
     */
    private async sendEmail(runId: number, results: any): Promise<void> {
        if (!this.emailTransporter || !this.config.email) return;

        const { summary, testResults, aiAnalysis } = results;
        const status = summary.status === 'PASSED' ? '✅ PASSED' : '❌ FAILED';

        const failedTests = testResults.filter((t: any) => t.status !== 'passed');

        let html = `
            <h2>Test Run #${runId} - ${status}</h2>
            <table border="1" cellpadding="8" cellspacing="0">
                <tr>
                    <td><strong>Total Tests</strong></td>
                    <td>${summary.total}</td>
                </tr>
                <tr>
                    <td><strong>Passed</strong></td>
                    <td style="color: green;">${summary.passed}</td>
                </tr>
                <tr>
                    <td><strong>Failed</strong></td>
                    <td style="color: red;">${summary.failed}</td>
                </tr>
                <tr>
                    <td><strong>Duration</strong></td>
                    <td>${(summary.duration / 1000).toFixed(2)}s</td>
                </tr>
            </table>
        `;

        if (failedTests.length > 0) {
            html += `
                <h3>Failed Tests</h3>
                <table border="1" cellpadding="8" cellspacing="0">
                    <tr>
                        <th>Test Name</th>
                        <th>Error</th>
                    </tr>
                    ${failedTests.map((t: any) => `
                        <tr>
                            <td>${t.testName}</td>
                            <td style="color: red;">${t.error || 'Unknown error'}</td>
                        </tr>
                    `).join('')}
                </table>
            `;
        }

        if (aiAnalysis) {
            html += `
                <h3>AI Analysis</h3>
                <p><strong>Summary:</strong> ${aiAnalysis.summary}</p>
                <p><strong>Suggestions:</strong></p>
                <ul>
                    ${aiAnalysis.suggestions.map((s: string) => `<li>${s}</li>`).join('')}
                </ul>
            `;
        }

        const appUrl = process.env.APP_URL || 'http://localhost:5173';
        html += `
            <p>
                <a href="${appUrl}/runs/${runId}">View Full Report</a>
            </p>
        `;

        try {
            await this.emailTransporter.sendMail({
                from: this.config.email.from,
                to: this.config.email.recipients.join(', '),
                subject: `[TraceIQ] Test Run #${runId} - ${status}`,
                html
            });
            console.log(`[NotificationService] Email sent for run ${runId}`);
        } catch (err) {
            console.error(`[NotificationService] Failed to send email for run ${runId}:`, err);
        }
    }

    /**
     * Send Slack notification
     */
    private async sendSlack(runId: number, results: any): Promise<void> {
        if (!this.config.slack?.webhookUrl) return;

        const { summary, aiAnalysis } = results;
        const status = summary.status === 'PASSED' ? '✅ Passed' : '❌ Failed';
        const color = summary.status === 'PASSED' ? 'good' : 'danger';

        const appUrl = process.env.APP_URL || 'http://localhost:5173';

        const payload = {
            channel: this.config.slack.channel,
            attachments: [{
                color,
                title: `Test Run #${runId} - ${status}`,
                title_link: `${appUrl}/runs/${runId}`,
                fields: [
                    { title: 'Total', value: summary.total.toString(), short: true },
                    { title: 'Passed', value: summary.passed.toString(), short: true },
                    { title: 'Failed', value: summary.failed.toString(), short: true },
                    { title: 'Duration', value: `${(summary.duration / 1000).toFixed(2)}s`, short: true }
                ],
                footer: 'TraceIQ',
                ts: Math.floor(Date.now() / 1000)
            }]
        };

        if (aiAnalysis?.summary) {
            payload.attachments[0].fields.push({
                title: 'AI Analysis',
                value: aiAnalysis.summary,
                short: false
            });
        }

        try {
            await fetch(this.config.slack.webhookUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            console.log(`[NotificationService] Slack notification sent for run ${runId}`);
        } catch (err) {
            console.error(`[NotificationService] Failed to send Slack for run ${runId}:`, err);
        }
    }

    /**
     * Send Microsoft Teams notification
     */
    private async sendTeams(runId: number, results: any): Promise<void> {
        if (!this.config.teams?.webhookUrl) return;

        const { summary } = results;
        const status = summary.status === 'PASSED' ? '✅ Passed' : '❌ Failed';
        const themeColor = summary.status === 'PASSED' ? '00FF00' : 'FF0000';

        const appUrl = process.env.APP_URL || 'http://localhost:5173';

        const payload = {
            '@type': 'MessageCard',
            '@context': 'http://schema.org/extensions',
            themeColor,
            summary: `Test Run #${runId} - ${status}`,
            sections: [{
                activityTitle: `Test Run #${runId} - ${status}`,
                facts: [
                    { name: 'Total Tests', value: summary.total.toString() },
                    { name: 'Passed', value: summary.passed.toString() },
                    { name: 'Failed', value: summary.failed.toString() },
                    { name: 'Duration', value: `${(summary.duration / 1000).toFixed(2)}s` }
                ],
                markdown: true
            }],
            potentialAction: [{
                '@type': 'OpenUri',
                name: 'View Report',
                targets: [{ os: 'default', uri: `${appUrl}/runs/${runId}` }]
            }]
        };

        try {
            await fetch(this.config.teams.webhookUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            console.log(`[NotificationService] Teams notification sent for run ${runId}`);
        } catch (err) {
            console.error(`[NotificationService] Failed to send Teams for run ${runId}:`, err);
        }
    }

    /**
     * Send custom webhook notification
     */
    private async sendWebhook(runId: number, results: any): Promise<void> {
        if (!this.config.webhook?.url) return;

        try {
            await fetch(this.config.webhook.url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...this.config.webhook.headers
                },
                body: JSON.stringify({
                    event: 'test_run_completed',
                    runId,
                    ...results
                })
            });
            console.log(`[NotificationService] Webhook sent for run ${runId}`);
        } catch (err) {
            console.error(`[NotificationService] Failed to send webhook for run ${runId}:`, err);
        }
    }
}
