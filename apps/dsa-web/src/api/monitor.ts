import apiClient from './index';

export interface MonitorStatus {
  timestamp: string;
  phase: 'offline' | 'pre_market' | 'live' | 'lunch_break' | 'closed';
  level: 'offline' | 'normal' | 'watch' | 'warning' | 'danger';
  confirmed?: boolean;
  reasons?: string[];
  snapshot?: {
    limit_down: number;
    limit_up: number;
    median_pct: number;
    csi300_pct: number;
    decline_ratio: number;
  };
  max_level_today?: string;
  // pre_market / closed fields
  date?: string;
  max_level_time?: string;
  peak_limit_down?: number;
  last_csi300?: number;
}

export async function getMonitorStatus(): Promise<MonitorStatus> {
  const res = await apiClient.get<MonitorStatus>('/api/v1/monitor/status');
  return res.data;
}
