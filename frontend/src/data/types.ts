/** The M3 API records one representative Incident for each Alarm episode. */
export type AlarmState = "ALARM" | "OK" | "INSUFFICIENT_DATA";

export interface IncidentSummary {
  incidentId: string;
  account: string;
  region: string;
  service: string;
  environment: string;
  alarmArn: string;
  alarmName: string;
  detectedAt: string;
  alarmState: AlarmState;
  incidentState: string;
  lastStateTransitionAt: string;
  originalEventRef: string;
  alarmOkAt?: string;
}

export interface AlarmStateHistoryEntry {
  eventId: string;
  state: AlarmState;
  transitionedAt: string;
  rawEvent: Record<string, unknown>;
}

export interface IncidentDetail extends IncidentSummary {
  stateHistory: AlarmStateHistoryEntry[];
}

export interface IncidentListResponse {
  items: IncidentSummary[];
  nextToken: string | null;
}

export type MetricKey = "errorRate" | "apiLatency" | "dbLatency";
export type MetricQuality = "ok" | "missing" | "low_volume";

/** Graph data is currently demo-only; M3 has no metrics read API. */
export interface MetricPoint {
  timestamp: string;
  value: number | null;
  quality: MetricQuality;
}

export interface MetricSeries {
  key: MetricKey;
  label: string;
  unit: "%" | "ms";
  /** A demonstration threshold, never an operational M2 configuration. */
  threshold?: number;
  points: MetricPoint[];
}
