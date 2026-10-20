/**
 * Validation runs (WP16c, decision V4): the backend dispatched this job to try
 * a PROPOSED selector heal / case edit, applied to this run only. Such a run
 * must neither self-heal at run time nor file new heal proposals — a
 * validation that only passed because the worker re-pointed a selector
 * validated nothing. The backend sets `settings.validation_run`; jobs from
 * older backends carry no flag and behave as before.
 */
export function isValidationJob(job: any): boolean {
    return job?.settings?.validation_run === true;
}
