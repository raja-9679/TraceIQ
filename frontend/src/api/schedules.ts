import { api } from '@/lib/api';
import type { Out, Schema } from './schemaTypes';

// Generated from the backend's TestScheduleRead. The hand-written interface had
// drifted: it lacked every monitor field (is_monitor, monitor_state,
// alert_emails, alert_after_failures, alert_on_recovery, last_checked_at,
// security_scan_target) and the created_by/updated_by ids.
export type TestSchedule = Out<'TestScheduleRead'>;
/** POST /schedules body. */
export type TestScheduleCreate = Schema<'TestScheduleBase'>;
/** PUT /schedules/{id} body — every field optional. */
export type TestScheduleUpdate = Schema<'TestScheduleUpdate'>;

export const schedulesApi = {
  list: async (projectId?: number): Promise<TestSchedule[]> => {
    const params = new URLSearchParams();
    if (projectId) params.append('project_id', projectId.toString());
    const response = await api.get(`/schedules?${params.toString()}`);
    return response.data;
  },
  
  get: async (id: number): Promise<TestSchedule> => {
    const response = await api.get(`/schedules/${id}`);
    return response.data;
  },
  
  create: async (data: TestScheduleCreate): Promise<TestSchedule> => {
    const response = await api.post('/schedules', data);
    return response.data;
  },
  
  update: async (id: number, data: TestScheduleUpdate): Promise<TestSchedule> => {
    const response = await api.put(`/schedules/${id}`, data);
    return response.data;
  },
  
  delete: async (id: number) => {
    const response = await api.delete(`/schedules/${id}`);
    return response.data;
  }
};
