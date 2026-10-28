import { useMemo } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { IncidentDetail, MetricSeries } from '../data/types';

type MetricChartProps = {
  series: MetricSeries;
  incident?: IncidentDetail;
  primary?: boolean;
  showEventLabels?: boolean;
};

const signalColors: Record<MetricSeries['key'], string> = {
  errorRate: '#ff7178',
  apiLatency: '#60d5e9',
  dbLatency: '#b59af8',
};

const eventColors = {
  ALARM: '#ff7178',
  OK: '#67d2a4',
  INSUFFICIENT_DATA: '#f0be62',
} as const;

function timeLabel(timestamp: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(timestamp));
}

function formatValue(value: number, unit: MetricSeries['unit']): string {
  return `${Number.isInteger(value) ? value : value.toFixed(1)}${unit}`;
}

function nearestTimestamp(series: MetricSeries, target: string): string | undefined {
  const at = new Date(target).getTime();
  const points = series.points;
  if (!points.length || at < new Date(points[0].timestamp).getTime() || at > new Date(points[points.length - 1].timestamp).getTime()) {
    return undefined;
  }
  return points.reduce((best, point) => {
    const distance = Math.abs(new Date(point.timestamp).getTime() - at);
    return distance < best.distance ? { timestamp: point.timestamp, distance } : best;
  }, { timestamp: points[0].timestamp, distance: Number.POSITIVE_INFINITY }).timestamp;
}

function unavailableRanges(series: MetricSeries): { start: string; end: string; reason: string }[] {
  const ranges: { start: string; end: string; reason: string }[] = [];
  const points = series.points;
  for (let i = 0; i < points.length;) {
    if (points[i].value !== null && points[i].quality === 'ok') {
      i += 1;
      continue;
    }
    const first = i;
    const reason = points[i].quality === 'low_volume' ? '표본 부족' : '데이터 없음';
    while (i < points.length && (points[i].value === null || points[i].quality !== 'ok')) i += 1;
    ranges.push({
      start: points[Math.max(0, first - 1)].timestamp,
      end: points[Math.min(points.length - 1, i)].timestamp,
      reason,
    });
  }
  return ranges;
}

export default function MetricChart({ series, incident, primary = false, showEventLabels = false }: MetricChartProps) {
  const peak = useMemo(() => Math.max(...series.points.flatMap(point => point.value === null ? [] : [point.value]), 0), [series]);
  const gaps = useMemo(() => unavailableRanges(series), [series]);
  const events = useMemo(() => (incident?.stateHistory ?? []).map(event => ({
    state: event.state,
    timestamp: nearestTimestamp(series, event.transitionedAt),
  })).filter((event): event is { state: IncidentDetail['stateHistory'][number]['state']; timestamp: string } => !!event.timestamp), [incident, series]);
  const alarm = events.find(event => event.state === 'ALARM');
  const ok = events.find(event => event.state === 'OK');
  const chartColor = signalColors[series.key];
  const chartHeight = primary ? 286 : 186;

  return (
    <Container
      className={`metric-panel${primary ? ' metric-panel-primary' : ''}`}
      header={
        <Header
          variant="h2"
          description={`${series.key === 'errorRate' ? '60초 집계' : '60초 평균'} · 서비스 지표 예시${series.threshold === undefined ? '' : ` · 예시 기준 ${formatValue(series.threshold, series.unit)}`}`}
          actions={<span className="metric-peak" style={{ color: chartColor }}>최고 {formatValue(peak, series.unit)}</span>}
        >
          {series.label}
        </Header>
      }
    >
      <figure className="metric-figure" aria-label={`${series.label} 시간별 추이, 예시 데이터`}>
        <ResponsiveContainer width="100%" height={chartHeight}>
          <LineChart data={series.points} margin={{ top: 20, right: 14, bottom: 6, left: 0 }} accessibilityLayer>
            <CartesianGrid stroke="#33465b" strokeOpacity={0.68} vertical={false} />
            <XAxis dataKey="timestamp" tickFormatter={timeLabel} stroke="#a8b8c9" tickLine={false} axisLine={false} minTickGap={26} fontSize={11} />
            <YAxis
              stroke="#a8b8c9"
              tickLine={false}
              axisLine={false}
              width={64}
              fontSize={11}
              tickFormatter={(value: number) => `${Math.round(value)}${series.unit}`}
              domain={[0, (dataMax: number) => Math.max(dataMax * 1.1, (series.threshold ?? 0) * 1.2, 1)]}
            />
            {alarm && <ReferenceArea x1={alarm.timestamp} x2={ok?.timestamp ?? series.points.at(-1)?.timestamp} fill="#ff7178" fillOpacity={0.075} strokeOpacity={0} />}
            {gaps.map((gap, index) => (
              <ReferenceArea key={`${gap.start}-${index}`} x1={gap.start} x2={gap.end} fill="#a8b8c9" fillOpacity={0.15} strokeOpacity={0} />
            ))}
            {series.threshold !== undefined && <ReferenceLine y={series.threshold} stroke="#f0be62" strokeDasharray="6 6" strokeWidth={1.4} />}
            {events.map((event, index) => (
              <ReferenceLine
                key={`${event.state}-${index}`}
                x={event.timestamp}
                stroke={eventColors[event.state]}
                strokeWidth={1.5}
                label={showEventLabels && event.state !== 'INSUFFICIENT_DATA' ? { value: event.state, position: 'top', fill: eventColors[event.state], fontSize: 11 } : undefined}
              />
            ))}
            <Tooltip
              contentStyle={{ background: '#1d2b3c', border: '1px solid #33465b', borderRadius: 4, color: '#f0f5fa' }}
              labelStyle={{ color: '#a8b8c9' }}
              labelFormatter={value => `${timeLabel(String(value))} KST`}
              formatter={value => [typeof value === 'number' ? formatValue(value, series.unit) : '데이터 없음', series.label]}
            />
            <Line dataKey="value" type="linear" stroke={chartColor} strokeWidth={primary ? 3 : 2.5} dot={false} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
        {gaps.length > 0 && <figcaption className="chart-caption">회색 구간은 이 예시 시계열에서 값이 없는 시간입니다. 운영 화면에서는 실제 지표 버킷의 결측 또는 표본 부족일 때만 표시합니다. Alarm의 INSUFFICIENT_DATA 상태만으로 결측 시간을 추정하지 않습니다.</figcaption>}
      </figure>
    </Container>
  );
}
