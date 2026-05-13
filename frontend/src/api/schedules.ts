import { api } from '@/lib/api';

export interface TestSchedule {
  id: number;
  name: string;
  description?: string;
  project_id: number;
  test_suite_id?: number;
  test_case_id?: number;
  browser: string;
  device?: string;
  cron_expression: string;
  is_active: boolean;
  next_run_at?: string;
  last_run_at?: string;
  created_at: string;
  updated_at: string;
}

export const schedulesApi = {
  list: async (projectId?: number) => {
    const params = new URLSearchParams();
    if (projectId) params.append('project_id', projectId.toString());
    const response = await api.get(`/schedules?${params.toString()}`);
    return response.data;
  },
  
  get: async (id: number) => {
    const response = await api.get(`/schedules/${id}`);
    return response.data;
  },
  
  create: async (data: Partial<TestSchedule>) => {
    const response = await api.post('/schedules', data);
    return response.data;
  },
  
  update: async (id: number, data: Partial<TestSchedule>) => {
    const response = await api.put(`/schedules/${id}`, data);
    return response.data;
  },
  
  delete: async (id: number) => {
    const response = await api.delete(`/schedules/${id}`);
    return response.data;
  }
};
