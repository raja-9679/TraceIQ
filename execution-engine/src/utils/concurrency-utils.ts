import * as os from 'os';

/**
 * Calculates the optimal concurrency limit for parallel test execution
 * based on system resources (CPU cores and available memory).
 * 
 * Priority order:
 * 1. PARALLEL_CONCURRENCY_LIMIT env var (manual override)
 * 2. Automatic calculation based on CPU and memory
 * 3. Safety bounds (min: 1, max: MAX_CONCURRENCY)
 * 
 * @returns Optimal number of concurrent browser contexts
 */
export function calculateOptimalConcurrency(): number {
    // Check for manual override first
    const envLimit = process.env.PARALLEL_CONCURRENCY_LIMIT;
    if (envLimit) {
        const parsed = parseInt(envLimit, 10);
        if (!isNaN(parsed) && parsed > 0) {
            console.log(`[Concurrency] Using manual override: ${parsed}`);
            return parsed;
        }
    }

    // Auto-calculate based on system resources
    const cpuCores = os.cpus().length;
    const totalMemoryMB = os.totalmem() / (1024 * 1024);
    const freeMemoryMB = os.freemem() / (1024 * 1024);

    // Conservative: use 50% of free memory
    // Each browser context uses approximately 300MB
    const memoryBasedLimit = Math.floor((freeMemoryMB * 0.5) / 300);

    // CPU-based: 2x cores (browser automation is I/O bound, not CPU bound)
    const cpuBasedLimit = cpuCores * 2;

    // Safety cap to prevent extreme values
    const maxSafeLimit = parseInt(process.env.MAX_CONCURRENCY || '10', 10);

    // Take the minimum of all constraints
    const calculated = Math.min(cpuBasedLimit, memoryBasedLimit, maxSafeLimit);

    // Ensure at least 1
    const final = Math.max(1, calculated);

    console.log(`[Concurrency] Auto-calculation:
    CPU cores: ${cpuCores} → limit: ${cpuBasedLimit}
    Total memory: ${totalMemoryMB.toFixed(0)}MB
    Free memory: ${freeMemoryMB.toFixed(0)}MB → limit: ${memoryBasedLimit}
    Max safe limit: ${maxSafeLimit}
    Final concurrency: ${final}`);

    return final;
}
