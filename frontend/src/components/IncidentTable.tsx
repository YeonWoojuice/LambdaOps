import Box from '@cloudscape-design/components/box';
import Header from '@cloudscape-design/components/header';
import Link from '@cloudscape-design/components/link';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import type { IncidentSummary } from '../data/types';

type IncidentTableProps = {
  items: IncidentSummary[];
  onSelect: (incidentId: string) => void;
  title?: string;
};

function alarmStatus(state: IncidentSummary['alarmState']) {
  const type = state === 'ALARM' ? 'error' : state === 'OK' ? 'success' : 'warning';
  return <StatusIndicator type={type}>{state}</StatusIndicator>;
}

function detectedAt(value: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

export default function IncidentTable({ items, onSelect, title = '최근 Incident' }: IncidentTableProps) {
  return (
    <Table<IncidentSummary>
      className="incident-table"
      items={items}
      trackBy="incidentId"
      stickyHeader
      variant="container"
      header={<Header variant="h2" description="이 페이지에 표시된 Incident만 집계합니다. 전체 건수는 아직 제공되지 않습니다." actions={<span className="table-page-count">현재 페이지 {items.length}건</span>}>{title}</Header>}
      columnDefinitions={[
        {
          id: 'alarm',
          header: 'Alarm',
          minWidth: 250,
          cell: (item: IncidentSummary) => (
            <div className="incident-alarm-cell">
              <Link href={`#/incidents/${item.incidentId}`} onFollow={event => { event.preventDefault(); onSelect(item.incidentId); }}>{item.alarmName}</Link>
              <span title={item.incidentId}>{`inc_…${item.incidentId.slice(-4)}`}</span>
            </div>
          ),
        },
        { id: 'alarmState', header: 'Alarm 상태', minWidth: 185, cell: (item: IncidentSummary) => alarmStatus(item.alarmState) },
        { id: 'incidentState', header: 'Incident 상태', minWidth: 150, cell: (item: IncidentSummary) => <StatusIndicator type="info">{item.incidentState}</StatusIndicator> },
        { id: 'service', header: '서비스', minWidth: 130, cell: (item: IncidentSummary) => item.service },
        { id: 'detectedAt', header: '감지 시각 (KST)', minWidth: 190, cell: (item: IncidentSummary) => detectedAt(item.detectedAt) },
      ]}
      empty={<Box textAlign="center" color="inherit"><b>표시할 Incident가 없습니다.</b><Box variant="p" color="text-body-secondary">목록 조회 결과가 비어 있습니다.</Box></Box>}
    />
  );
}
