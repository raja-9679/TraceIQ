/**
 * AI Analyzer - AI-powered analysis of test failures
 * 
 * Capabilities:
 * 1. Analyze console errors and logs
 * 2. Identify root cause of failures
 * 3. Suggest fixes based on error patterns
 * 4. Detect flaky tests
 * 5. Categorize failures (network, timeout, assertion, etc.)
 */

export interface FailureAnalysis {
    runId: number;
    summary: string;
    failedTests: TestFailureAnalysis[];
    rootCauses: RootCause[];
    suggestions: string[];
    flakyTestsDetected: string[];
}

export interface TestFailureAnalysis {
    testName: string;
    errorType: ErrorType;
    errorMessage: string;
    rootCause: string;
    suggestedFix: string;
    relatedLogs: string[];
    confidence: number; // 0-1 confidence in analysis
}

export interface RootCause {
    category: string;
    description: string;
    affectedTests: string[];
    priority: 'high' | 'medium' | 'low';
}

export enum ErrorType {
    TIMEOUT = 'timeout',
    ELEMENT_NOT_FOUND = 'element_not_found',
    NETWORK_ERROR = 'network_error',
    ASSERTION_FAILED = 'assertion_failed',
    JAVASCRIPT_ERROR = 'javascript_error',
    AUTHENTICATION = 'authentication',
    NAVIGATION = 'navigation',
    UNKNOWN = 'unknown'
}

export class AIAnalyzer {
    private openaiApiKey: string | null;
    private enabled: boolean;

    constructor() {
        this.openaiApiKey = process.env.OPENAI_API_KEY || null;
        this.enabled = process.env.AI_ANALYSIS_ENABLED === 'true';
    }

    /**
     * Analyze failures in a test run
     */
    async analyzeFailures(runId: number, results: any[]): Promise<FailureAnalysis> {
        console.log(`[AIAnalyzer] Analyzing failures for run ${runId}`);

        const failedResults = results.filter(r => r.status !== 'passed');
        
        // Basic analysis (always available)
        const basicAnalysis = this.performBasicAnalysis(runId, failedResults);

        // AI-powered analysis (if enabled and API key available)
        if (this.enabled && this.openaiApiKey) {
            try {
                const aiEnhancedAnalysis = await this.performAIAnalysis(runId, failedResults, basicAnalysis);
                return aiEnhancedAnalysis;
            } catch (err) {
                console.error('[AIAnalyzer] AI analysis failed, using basic analysis:', err);
            }
        }

        return basicAnalysis;
    }

    /**
     * Basic pattern-matching analysis (no AI)
     */
    private performBasicAnalysis(runId: number, failedResults: any[]): FailureAnalysis {
        const failedTests: TestFailureAnalysis[] = [];
        const rootCauses: Map<string, RootCause> = new Map();

        for (const result of failedResults) {
            const errorType = this.classifyError(result.error || '');
            const analysis: TestFailureAnalysis = {
                testName: result.test_name,
                errorType,
                errorMessage: result.error || 'Unknown error',
                rootCause: this.getRootCauseDescription(errorType),
                suggestedFix: this.getSuggestedFix(errorType, result.error),
                relatedLogs: [],
                confidence: 0.6 // Basic analysis has lower confidence
            };
            failedTests.push(analysis);

            // Group by error type
            const causeKey = errorType;
            if (!rootCauses.has(causeKey)) {
                rootCauses.set(causeKey, {
                    category: errorType,
                    description: this.getRootCauseDescription(errorType),
                    affectedTests: [],
                    priority: this.getErrorPriority(errorType)
                });
            }
            rootCauses.get(causeKey)!.affectedTests.push(result.test_name);
        }

        return {
            runId,
            summary: this.generateSummary(failedTests),
            failedTests,
            rootCauses: Array.from(rootCauses.values()),
            suggestions: this.generateSuggestions(failedTests),
            flakyTestsDetected: [] // Requires historical data
        };
    }

    /**
     * AI-powered analysis using OpenAI
     */
    private async performAIAnalysis(
        runId: number, 
        failedResults: any[], 
        basicAnalysis: FailureAnalysis
    ): Promise<FailureAnalysis> {
        
        const prompt = this.buildAnalysisPrompt(failedResults);
        
        const response = await fetch('https://api.openai.com/v1/chat/completions', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${this.openaiApiKey}`
            },
            body: JSON.stringify({
                model: 'gpt-4',
                messages: [
                    {
                        role: 'system',
                        content: `You are an expert test automation engineer analyzing test failures. 
                        Provide concise, actionable analysis of test failures.
                        Focus on identifying root causes and suggesting specific fixes.
                        Format your response as JSON matching the FailureAnalysis interface.`
                    },
                    {
                        role: 'user',
                        content: prompt
                    }
                ],
                temperature: 0.3,
                max_tokens: 2000
            })
        });

        if (!response.ok) {
            throw new Error(`OpenAI API error: ${response.status}`);
        }

        const data = await response.json();
        const aiContent = data.choices[0]?.message?.content;

        if (aiContent) {
            try {
                const aiAnalysis = JSON.parse(aiContent);
                return {
                    ...basicAnalysis,
                    ...aiAnalysis,
                    runId // Ensure runId is preserved
                };
            } catch (parseErr) {
                console.error('[AIAnalyzer] Failed to parse AI response:', parseErr);
            }
        }

        return basicAnalysis;
    }

    private buildAnalysisPrompt(failedResults: any[]): string {
        const failures = failedResults.map(r => ({
            testName: r.test_name,
            error: r.error,
            duration: r.duration_ms
        }));

        return `Analyze these test failures and provide insights:

${JSON.stringify(failures, null, 2)}

Provide:
1. Summary of the failures (1-2 sentences)
2. Root cause analysis for each failure
3. Suggested fixes
4. Common patterns across failures`;
    }

    /**
     * Classify error type based on error message
     */
    private classifyError(error: string): ErrorType {
        const lowerError = error.toLowerCase();

        if (lowerError.includes('timeout') || lowerError.includes('exceeded')) {
            return ErrorType.TIMEOUT;
        }
        if (lowerError.includes('not found') || lowerError.includes('no element') || 
            lowerError.includes('unable to locate')) {
            return ErrorType.ELEMENT_NOT_FOUND;
        }
        if (lowerError.includes('network') || lowerError.includes('net::') || 
            lowerError.includes('connection')) {
            return ErrorType.NETWORK_ERROR;
        }
        if (lowerError.includes('assert') || lowerError.includes('expect')) {
            return ErrorType.ASSERTION_FAILED;
        }
        if (lowerError.includes('javascript') || lowerError.includes('script error') ||
            lowerError.includes('uncaught')) {
            return ErrorType.JAVASCRIPT_ERROR;
        }
        if (lowerError.includes('auth') || lowerError.includes('login') || 
            lowerError.includes('401') || lowerError.includes('403')) {
            return ErrorType.AUTHENTICATION;
        }
        if (lowerError.includes('navigation') || lowerError.includes('goto')) {
            return ErrorType.NAVIGATION;
        }

        return ErrorType.UNKNOWN;
    }

    private getRootCauseDescription(errorType: ErrorType): string {
        const descriptions: Record<ErrorType, string> = {
            [ErrorType.TIMEOUT]: 'Element or operation took too long to complete',
            [ErrorType.ELEMENT_NOT_FOUND]: 'UI element not present or selector changed',
            [ErrorType.NETWORK_ERROR]: 'Network connectivity or API failure',
            [ErrorType.ASSERTION_FAILED]: 'Expected value did not match actual value',
            [ErrorType.JAVASCRIPT_ERROR]: 'JavaScript error on the page',
            [ErrorType.AUTHENTICATION]: 'Authentication or authorization issue',
            [ErrorType.NAVIGATION]: 'Page navigation or loading failure',
            [ErrorType.UNKNOWN]: 'Unknown error - requires manual investigation'
        };
        return descriptions[errorType];
    }

    private getSuggestedFix(errorType: ErrorType, error: string): string {
        const fixes: Record<ErrorType, string> = {
            [ErrorType.TIMEOUT]: 'Increase timeout, add explicit waits, or check element visibility',
            [ErrorType.ELEMENT_NOT_FOUND]: 'Update selector, verify element exists, check for dynamic loading',
            [ErrorType.NETWORK_ERROR]: 'Check API health, verify network connectivity, retry with backoff',
            [ErrorType.ASSERTION_FAILED]: 'Review expected vs actual values, check test data',
            [ErrorType.JAVASCRIPT_ERROR]: 'Check console logs, verify page JavaScript is functional',
            [ErrorType.AUTHENTICATION]: 'Verify credentials, check session handling, review auth flow',
            [ErrorType.NAVIGATION]: 'Verify URL, check redirects, ensure page loads completely',
            [ErrorType.UNKNOWN]: 'Review full error logs and stack trace'
        };
        return fixes[errorType];
    }

    private getErrorPriority(errorType: ErrorType): 'high' | 'medium' | 'low' {
        const highPriority = [ErrorType.AUTHENTICATION, ErrorType.NETWORK_ERROR];
        const mediumPriority = [ErrorType.ELEMENT_NOT_FOUND, ErrorType.TIMEOUT, ErrorType.NAVIGATION];
        
        if (highPriority.includes(errorType)) return 'high';
        if (mediumPriority.includes(errorType)) return 'medium';
        return 'low';
    }

    private generateSummary(failedTests: TestFailureAnalysis[]): string {
        if (failedTests.length === 0) return 'All tests passed';
        
        const errorTypes = new Set(failedTests.map(t => t.errorType));
        const primaryType = Array.from(errorTypes)[0];
        
        return `${failedTests.length} test(s) failed. Primary issue: ${this.getRootCauseDescription(primaryType)}`;
    }

    private generateSuggestions(failedTests: TestFailureAnalysis[]): string[] {
        const suggestions = new Set<string>();
        
        for (const test of failedTests) {
            suggestions.add(test.suggestedFix);
        }
        
        return Array.from(suggestions);
    }

    /**
     * Analyze console logs for errors and warnings
     */
    async analyzeConsoleLogs(logs: any[]): Promise<any> {
        const errors = logs.filter(l => l.type === 'error');
        const warnings = logs.filter(l => l.type === 'warning');

        return {
            totalLogs: logs.length,
            errors: errors.length,
            warnings: warnings.length,
            criticalErrors: errors.filter(e => 
                e.text?.includes('Uncaught') || 
                e.text?.includes('TypeError') ||
                e.text?.includes('ReferenceError')
            ),
            summary: errors.length > 0 
                ? `Found ${errors.length} console errors that may affect test stability`
                : 'No critical console errors detected'
        };
    }
}
