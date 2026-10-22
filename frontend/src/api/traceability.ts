import { api } from '@/lib/api';
import { listProjectCases } from './journeyReports';

// Mirrors backend/app/api/traceability.py

export interface RequirementCoverage {
    ref: string;
    source: string;
    title?: string | null;
    url?: string | null;
    test_count: number;
    status: 'passing' | 'failing' | 'mixed' | 'unknown';
    passing: number;
    failing: number;
    untested: number;
    test_names: string[];
}

export interface TraceabilityGaps {
    project_id: number;
    total_cases: number;
    traced_cases: number;
    untraced_cases: { id: number; name: string }[];
}

export interface RequirementLink {
    id: number;
    test_case_id: number;
    ref: string;
    source: string;
    title?: string | null;
    url?: string | null;
    created_at: string;
}

export const traceApi = {
    requirements: async (projectId: number): Promise<RequirementCoverage[]> =>
        (await api.get(`/projects/${projectId}/requirements`)).data,
    gaps: async (projectId: number): Promise<TraceabilityGaps> =>
        (await api.get(`/projects/${projectId}/traceability/gaps`)).data,
    caseLinks: async (caseId: number): Promise<RequirementLink[]> =>
        (await api.get(`/cases/${caseId}/requirements`)).data,
    addLink: async (caseId: number, body: { ref: string; source?: string; title?: string; url?: string }): Promise<RequirementLink> =>
        (await api.post(`/cases/${caseId}/requirements`, body)).data,
    removeLink: async (caseId: number, linkId: number): Promise<void> => {
        await api.delete(`/cases/${caseId}/requirements/${linkId}`);
    },
};

/** One test's link to a requirement ref, with what removing it needs. */
export interface RefLink { caseId: number; caseName: string; linkId: number }

/**
 * The links behind one row of the coverage rollup. The rollup only carries
 * test NAMES, so this lists the project's cases, keeps those named, and reads
 * each one's links to find the link id for `ref`.
 */
export async function linksForRef(projectId: number, ref: string, names: string[]): Promise<RefLink[]> {
    const wanted = new Set(names);
    const cases = (await listProjectCases(projectId)).filter((c) => wanted.has(c.name));
    const out: RefLink[] = [];
    for (const c of cases) {
        for (const l of await traceApi.caseLinks(c.id)) {
            if (l.ref === ref) out.push({ caseId: c.id, caseName: c.name, linkId: l.id });
        }
    }
    return out;
}
