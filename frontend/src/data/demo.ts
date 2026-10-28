import type {
  AlarmState,
  AlarmStateHistoryEntry,
  IncidentDetail,
  IncidentSummary,
  MetricKey,
  MetricSeries,
} from "./types";

const account = "123456789012";
const region = "ap-northeast-2";
const environment = "dev";
const apiService = "slash-api";
const dbService = "slash-db";

const errorIncidentId = "inc_00000000000000000000000000000001";
const latencyIncidentId = "inc_00000000000000000000000000000002";
const dbIncidentId = "inc_00000000000000000000000000000003";

const detectedAt = "2026-10-28T04:55:00Z"; // 13:55 KST
const insufficientAt = "2026-10-28T05:10:00Z"; // 14:10 KST
const okAt = "2026-10-28T05:25:00Z"; // 14:25 KST

const eventIds = {
  errorAlarm: "11111111-1111-4111-8111-111111111111",
  errorOk: "11111111-1111-4111-8111-111111111112",
  latencyAlarm: "22222222-2222-4222-8222-222222222221",
  latencyInsufficient: "22222222-2222-4222-8222-222222222222",
  latencyOk: "22222222-2222-4222-8222-222222222223",
  dbAlarm: "33333333-3333-4333-8333-333333333331",
  dbInsufficient: "33333333-3333-4333-8333-333333333332",
} as const;

const summaries: IncidentSummary[] = [
  {
    incidentId: errorIncidentId,
    account,
    region,
    service: apiService,
    environment,
    alarmArn: `arn:aws:cloudwatch:${region}:${account}:alarm:slash-api-5xx-rate`,
    alarmName: "API 5xx 비율 증가",
    detectedAt,
    alarmState: "OK",
    incidentState: "NEW",
    lastStateTransitionAt: okAt,
    originalEventRef: eventIds.errorAlarm,
    alarmOkAt: okAt,
  },
  {
    incidentId: latencyIncidentId,
    account,
    region,
    service: apiService,
    environment,
    alarmArn: `arn:aws:cloudwatch:${region}:${account}:alarm:slash-api-latency`,
    alarmName: "API 평균 지연 증가",
    detectedAt,
    alarmState: "OK",
    incidentState: "NEW",
    lastStateTransitionAt: okAt,
    originalEventRef: eventIds.latencyAlarm,
    alarmOkAt: okAt,
  },
  {
    incidentId: dbIncidentId,
    account,
    region,
    service: dbService,
    environment,
    alarmArn: `arn:aws:cloudwatch:${region}:${account}:alarm:slash-db-latency`,
    alarmName: "DB 쿼리 평균 지연",
    detectedAt: "2026-10-28T03:18:00Z", // 12:18 KST
    alarmState: "INSUFFICIENT_DATA",
    incidentState: "NEW",
    lastStateTransitionAt: "2026-10-28T03:40:00Z",
    originalEventRef: eventIds.dbAlarm,
  },
];

export const demoIncidents: IncidentSummary[] = summaries;

function historyEntry(
  alarmName: string,
  alarmArn: string,
  eventId: string,
  state: AlarmState,
  transitionedAt: string,
): AlarmStateHistoryEntry {
  return {
    eventId,
    state,
    transitionedAt,
    rawEvent: {
      id: eventId,
      source: "aws.cloudwatch",
      "detail-type": "CloudWatch Alarm State Change",
      account,
      region,
      time: transitionedAt,
      resources: [alarmArn],
      detail: {
        alarmName,
        state: { value: state, timestamp: transitionedAt },
      },
    },
  };
}

function detail(
  summary: IncidentSummary,
  states: ReadonlyArray<readonly [string, AlarmState, string]>,
): IncidentDetail {
  return {
    ...summary,
    stateHistory: states.map(([eventId, state, transitionedAt]) =>
      historyEntry(summary.alarmName, summary.alarmArn, eventId, state, transitionedAt),
    ),
  };
}

export const demoIncidentDetails: Record<string, IncidentDetail> = {
  [errorIncidentId]: detail(summaries[0], [
    [eventIds.errorAlarm, "ALARM", detectedAt],
    [eventIds.errorOk, "OK", okAt],
  ]),
  [latencyIncidentId]: detail(summaries[1], [
    [eventIds.latencyAlarm, "ALARM", detectedAt],
    [eventIds.latencyInsufficient, "INSUFFICIENT_DATA", insufficientAt],
    [eventIds.latencyOk, "OK", okAt],
  ]),
  [dbIncidentId]: detail(summaries[2], [
    [eventIds.dbAlarm, "ALARM", "2026-10-28T03:18:00Z"],
    [eventIds.dbInsufficient, "INSUFFICIENT_DATA", "2026-10-28T03:40:00Z"],
  ]),
};

export const demoIncidentDetail: IncidentDetail = demoIncidentDetails[latencyIncidentId];

type Anchor = readonly [minute: number, value: number];

function interpolatedValue(minute: number, anchors: ReadonlyArray<Anchor>): number {
  for (let i = 1; i < anchors.length; i += 1) {
    const [rightMinute, rightValue] = anchors[i];
    if (minute <= rightMinute) {
      const [leftMinute, leftValue] = anchors[i - 1];
      const fraction = (minute - leftMinute) / (rightMinute - leftMinute);
      return Math.round((leftValue + (rightValue - leftValue) * fraction) * 10) / 10;
    }
  }
  return anchors[anchors.length - 1][1];
}

function series(
  key: MetricKey,
  label: string,
  unit: "%" | "ms",
  threshold: number,
  startUtc: string,
  anchors: ReadonlyArray<Anchor>,
  missingMinutes: ReadonlySet<number> = new Set(),
): MetricSeries {
  const start = Date.parse(startUtc);
  return {
    key,
    label,
    unit,
    threshold,
    points: Array.from({ length: 61 }, (_, minute) => {
      const missing = missingMinutes.has(minute);
      return {
        timestamp: new Date(start + minute * 60_000).toISOString(),
        value: missing ? null : interpolatedValue(minute, anchors),
        quality: missing ? "missing" : "ok",
      };
    }),
  };
}

const overviewStart = "2026-10-28T04:30:00Z"; // 13:30 KST
const latencyGap = new Set(Array.from({ length: 9 }, (_, index) => 36 + index));

export const demoMetricSeries: MetricSeries[] = [
  series("errorRate", "API 5xx 비율", "%", 5, overviewStart, [
    [0, 0.4], [20, 1.2], [25, 5.8], [30, 8.7], [35, 7.9],
    [40, 9.1], [45, 6.5], [50, 3], [55, 1.2], [60, 0.7],
  ]),
  series("apiLatency", "API 평균 지연", "ms", 400, overviewStart, [
    [0, 125], [20, 180], [25, 420], [30, 510], [35, 495],
    [45, 470], [50, 365], [55, 250], [60, 170],
  ], latencyGap),
  series("dbLatency", "DB 쿼리 평균 지연", "ms", 200, overviewStart, [
    [0, 58], [20, 78], [25, 150], [30, 195], [35, 220],
    [40, 205], [45, 185], [50, 140], [55, 100], [60, 80],
  ]),
];

const dbStart = "2026-10-28T02:50:00Z"; // 11:50 KST
export const demoMetricSeriesByIncident: Record<string, MetricSeries[]> = {
  [errorIncidentId]: demoMetricSeries,
  [latencyIncidentId]: demoMetricSeries,
  // This example intentionally has no metric gap despite INSUFFICIENT_DATA.
  [dbIncidentId]: [
    series("errorRate", "API 5xx 비율", "%", 5, dbStart, [[0, 0.3], [30, 0.6], [60, 0.4]]),
    series("apiLatency", "API 평균 지연", "ms", 400, dbStart, [[0, 110], [30, 150], [60, 120]]),
    series("dbLatency", "DB 쿼리 평균 지연", "ms", 200, dbStart, [
      [0, 60], [20, 100], [28, 230], [35, 270], [45, 245], [55, 190], [60, 165],
    ]),
  ],
};
