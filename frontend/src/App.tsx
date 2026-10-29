import { useEffect, useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import AppLayoutToolbar from '@cloudscape-design/components/app-layout-toolbar';
import BreadcrumbGroup from '@cloudscape-design/components/breadcrumb-group';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SideNavigation from '@cloudscape-design/components/side-navigation';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import { demoIncidentDetails, demoIncidents, demoMetricSeries, demoMetricSeriesByIncident } from './data/demo';
import type { IncidentDetail, IncidentListResponse, IncidentSummary, MetricSeries } from './data/types';
import AlarmTimeline from './components/AlarmTimeline';
import IncidentTable from './components/IncidentTable';
import MetricChart from './components/MetricChart';
import './styles.css';

type IncidentSource = {
  listIncidents: (options?: { page?: number; pageSize?: number; signal?: AbortSignal }) => Promise<IncidentListResponse>;
  getIncident: (incidentId: string, signal?: AbortSignal) => Promise<IncidentDetail>;
};

type AppProps = { incidentSource?: IncidentSource };
type Route = { page: 'overview' | 'incidents' } | { page: 'detail'; incidentId: string };

function currentRoute(): Route {
  const hash = window.location.hash.replace(/^#\/?/, '');
  if (hash.startsWith('incidents/')) {
    try {
      return { page: 'detail', incidentId: decodeURIComponent(hash.slice('incidents/'.length)) };
    } catch {
      return { page: 'detail', incidentId: '' };
    }
  }
  if (hash === 'incidents') return { page: 'incidents' };
  return { page: 'overview' };
}

function navigate(path: string) {
  window.location.hash = `#/${path}`;
}

function metric(series: MetricSeries[], key: MetricSeries['key']): MetricSeries | undefined {
  return series.find(item => item.key === key);
}

function alarmIndicator(state: IncidentSummary['alarmState']) {
  return <StatusIndicator type={state === 'ALARM' ? 'error' : state === 'OK' ? 'success' : 'warning'}>Alarm {state}</StatusIndicator>;
}

function dateTime(value: string | undefined): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value));
}

function DemoNotice() {
  return <Alert type="info" header="예시 데이터 · CloudWatch 그래프 연결 전">그래프 값과 예시 임계선은 화면 검토용입니다. 실제 운영 지표로 해석하지 마세요.</Alert>;
}

function NoMetrics() {
  return (
    <Container header={<Header variant="h2">CloudWatch 시계열</Header>}>
      <div className="metrics-pending">
        <strong>지표 조회 API 연결 전입니다.</strong>
        <p>현재 Incident API로는 상태 전환 이력을 확인할 수 있습니다. 그래프는 인증된 CloudWatch 지표 조회 경로가 준비되면 표시됩니다.</p>
      </div>
    </Container>
  );
}

function Overview({ items, totalItems, sourceIsDemo, onSelect }: { items: IncidentSummary[]; totalItems?: number; sourceIsDemo: boolean; onSelect: (id: string) => void }) {
  const errorRate = metric(demoMetricSeries, 'errorRate');
  const apiLatency = metric(demoMetricSeries, 'apiLatency');
  const dbLatency = metric(demoMetricSeries, 'dbLatency');
  const errorIncident = demoIncidentDetails[demoIncidents[0].incidentId];
  const apiLatencyIncident = demoIncidentDetails[demoIncidents[1].incidentId];
  const dbLatencyIncident = demoIncidentDetails[demoIncidents[2].incidentId];

  return (
    <ContentLayout header={<Header variant="h1" description="지표의 변화와 Alarm 에피소드를 같은 시간축에서 살펴봅니다." actions={<span className="review-chip">{sourceIsDemo ? '목표 시안' : 'Incident 조회 연결'}</span>}>Slash API observability</Header>}>
      <SpaceBetween size="l">
        {sourceIsDemo ? <DemoNotice /> : <Alert type="info">Incident 목록은 API에서 읽고 있습니다. CloudWatch 시계열 조회 경로는 아직 연결되지 않았습니다.</Alert>}
        {sourceIsDemo && errorRate && apiLatency && dbLatency ? (
          <>
            <MetricChart series={errorRate} incident={errorIncident} primary showEventLabels />
            <div className="metric-grid">
              <MetricChart series={apiLatency} incident={apiLatencyIncident} />
              <MetricChart series={dbLatency} incident={dbLatencyIncident} />
            </div>
          </>
        ) : <NoMetrics />}
        <IncidentTable items={items} totalItems={totalItems} onSelect={onSelect} />
      </SpaceBetween>
    </ContentLayout>
  );
}

function IncidentList({ items, page, totalItems, onSelect, sourceIsDemo, onNext, onPrevious, hasNext, hasPrevious }: {
  items: IncidentSummary[];
  page?: number;
  totalItems?: number;
  onSelect: (id: string) => void;
  sourceIsDemo: boolean;
  onNext: () => void;
  onPrevious: () => void;
  hasNext: boolean;
  hasPrevious: boolean;
}) {
  return (
    <ContentLayout header={<Header variant="h1" description="Alarm 에피소드마다 목록에 보이는 대표 Incident 한 건을 확인합니다.">Incidents</Header>}>
      <SpaceBetween size="l">
        {sourceIsDemo && <DemoNotice />}
        <IncidentTable title="Incident 목록" items={items} page={page} totalItems={totalItems} onSelect={onSelect} />
        {(hasNext || hasPrevious) && (
          <div className="page-controls">
            <Button disabled={!hasPrevious} onClick={onPrevious}>이전</Button>
            <Button disabled={!hasNext} onClick={onNext}>다음</Button>
          </div>
        )}
      </SpaceBetween>
    </ContentLayout>
  );
}

function IncidentDetails({ incident, sourceIsDemo }: { incident: IncidentDetail; sourceIsDemo: boolean }) {
  const sampleSeries = demoMetricSeriesByIncident[incident.incidentId];
  const primaryKey: MetricSeries['key'] = incident.alarmName.includes('5xx') ? 'errorRate' : incident.alarmName.includes('DB') ? 'dbLatency' : 'apiLatency';
  const primarySeries = sampleSeries && metric(sampleSeries, primaryKey);
  const supportingSeries = sampleSeries?.filter(series => series.key !== primaryKey) ?? [];
  const showSampleMetrics = sourceIsDemo && !!primarySeries;

  return (
    <ContentLayout header={<Header variant="h1" description={<span title={incident.incidentId}>{incident.incidentId} · {incident.service} / {incident.environment} · 감지 {dateTime(incident.detectedAt)} KST</span>} actions={<div className="detail-state"><span>{alarmIndicator(incident.alarmState)}</span><StatusIndicator type="info">Incident {incident.incidentState}</StatusIndicator></div>}>{incident.alarmName}</Header>}>
      <SpaceBetween size="l">
        <Alert type="info" header="Alarm 상태와 업무 상태는 별개입니다">Alarm OK는 해당 Alarm 에피소드의 종료만 뜻합니다. 서비스 전체 복구나 Incident 해결의 증거로 취급하지 않습니다. INSUFFICIENT_DATA도 복구가 아닙니다.</Alert>
        {sourceIsDemo && <DemoNotice />}
        {!sourceIsDemo && incident.redirected && incident.requestedIncidentId && (
          <Alert type="info" header="대표 Incident로 연결됨">
            요청한 {incident.requestedIncidentId}의 현재 대표 기록은 {incident.incidentId}입니다.
          </Alert>
        )}
        {showSampleMetrics && primarySeries ? (
          <>
            <div className="detail-primary-grid">
              <MetricChart series={primarySeries} incident={incident} primary showEventLabels />
              <AlarmTimeline incident={incident} />
            </div>
            <div className="metric-grid">
              {supportingSeries.map(series => <MetricChart key={series.key} series={series} incident={incident} />)}
            </div>
          </>
        ) : (
          <div className="detail-primary-grid">
            <NoMetrics />
            <AlarmTimeline incident={incident} />
          </div>
        )}
        <Container header={<Header variant="h2">Incident 정보</Header>}>
          <KeyValuePairs
            columns={4}
            items={[
              { label: 'Incident ID', value: <span className="copyable-id">{incident.incidentId}</span> },
              { label: 'Alarm ARN', value: <span className="copyable-id">{incident.alarmArn}</span> },
              { label: '서비스 · 환경', value: `${incident.service} · ${incident.environment}` },
              { label: '계정 · 리전', value: `${incident.account} · ${incident.region}` },
              { label: 'OK 전환', value: dateTime(incident.alarmOkAt) },
              { label: '최근 상태 전환', value: dateTime(incident.lastStateTransitionAt) },
            ]}
          />
        </Container>
      </SpaceBetween>
    </ContentLayout>
  );
}

export default function App({ incidentSource }: AppProps) {
  const sourceIsDemo = incidentSource === undefined;
  const [route, setRoute] = useState<Route>(currentRoute);
  const [items, setItems] = useState<IncidentSummary[]>(sourceIsDemo ? demoIncidents : []);
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [listPage, setListPage] = useState(1);
  const [hasNextPage, setHasNextPage] = useState(false);
  const [totalItems, setTotalItems] = useState<number | undefined>();
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<{ incidentId: string; message: string } | null>(null);
  const [navigationOpen, setNavigationOpen] = useState(true);

  useEffect(() => {
    const onHashChange = () => setRoute(currentRoute());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  useEffect(() => {
    if (route.page === 'overview') setListPage(1);
  }, [route.page]);

  useEffect(() => {
    if (!incidentSource) return;
    const controller = new AbortController();
    setListLoading(true);
    setListError(null);
    incidentSource.listIncidents({ page: listPage, pageSize: 50, signal: controller.signal })
      .then(response => { setItems(response.items); setHasNextPage(response.hasNextPage); setTotalItems(response.totalItems); })
      .catch(() => { if (!controller.signal.aborted) { setItems([]); setHasNextPage(false); setTotalItems(undefined); setListError('Incident 목록을 읽지 못했습니다. 인증과 연결 상태를 확인해 주세요.'); } })
      .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [incidentSource, listPage]);

  useEffect(() => {
    if (route.page !== 'detail') return;
    if (!incidentSource) return;
    const controller = new AbortController();
    setDetail(null);
    setDetailError(null);
    incidentSource.getIncident(route.incidentId, controller.signal)
      .then(setDetail)
      .catch(() => { if (!controller.signal.aborted) setDetailError({ incidentId: route.incidentId, message: 'Incident 상세를 읽지 못했습니다. 인증과 연결 상태를 확인해 주세요.' }); });
    return () => controller.abort();
  }, [incidentSource, route]);

  const routeDetail = route.page === 'detail'
    ? (sourceIsDemo ? demoIncidentDetails[route.incidentId] ?? null : (detail?.requestedIncidentId ?? detail?.incidentId) === route.incidentId ? detail : null)
    : null;
  const currentDetailError = route.page === 'detail' && detailError?.incidentId === route.incidentId ? detailError.message : null;
  const activeHref = route.page === 'overview' ? '#/overview' : '#/incidents';
  const goNext = () => {
    if (!hasNextPage || listLoading) return;
    setHasNextPage(false);
    setListPage(page => page + 1);
  };
  const goPrevious = () => {
    if (listPage <= 1 || listLoading) return;
    setHasNextPage(false);
    setListPage(page => page - 1);
  };
  const breadcrumbs = route.page === 'detail'
    ? [{ text: 'LambdaOps', href: '#/overview' }, { text: 'Incidents', href: '#/incidents' }, { text: routeDetail?.alarmName ?? route.incidentId, href: `#/incidents/${route.incidentId}` }]
    : [{ text: 'LambdaOps', href: '#/overview' }, { text: route.page === 'overview' ? '운영 개요' : 'Incidents', href: activeHref }];

  let content;
  if (route.page === 'overview') {
    content = <Overview items={items} totalItems={totalItems} sourceIsDemo={sourceIsDemo} onSelect={id => navigate(`incidents/${id}`)} />;
  } else if (route.page === 'incidents') {
    content = <IncidentList items={items} page={sourceIsDemo ? undefined : listPage} totalItems={totalItems} sourceIsDemo={sourceIsDemo} onSelect={id => navigate(`incidents/${id}`)} hasNext={hasNextPage && !listLoading} hasPrevious={listPage > 1 && !listLoading} onNext={goNext} onPrevious={goPrevious} />;
  } else if (routeDetail) {
    content = <IncidentDetails incident={routeDetail} sourceIsDemo={sourceIsDemo} />;
  } else {
    const detailMessage = currentDetailError ?? (sourceIsDemo ? '해당 Incident를 찾을 수 없습니다.' : 'Incident를 읽는 중입니다.');
    content = <ContentLayout header={<Header variant="h1">Incident 상세</Header>}><Alert type={currentDetailError ? 'error' : sourceIsDemo ? 'warning' : 'info'}>{detailMessage}</Alert></ContentLayout>;
  }

  return (
    <div className="lambdaops-app">
      <TopNavigation identity={{ href: '#/overview', title: 'LambdaOps' }} />
      <AppLayoutToolbar
        contentType="dashboard"
        maxContentWidth={1600}
        navigationWidth={224}
        navigationOpen={navigationOpen}
        onNavigationChange={({ detail: change }) => setNavigationOpen(change.open)}
        navigation={<SideNavigation activeHref={activeHref} header={{ href: '#/overview', text: 'WORKSPACE' }} items={[{ type: 'link', text: '운영 개요', href: '#/overview' }, { type: 'link', text: 'Incidents', href: '#/incidents' }]} onFollow={event => { event.preventDefault(); navigate(event.detail.href?.replace(/^#\//, '') ?? 'overview'); }} />}
        breadcrumbs={<BreadcrumbGroup items={breadcrumbs} onFollow={event => { event.preventDefault(); navigate(event.detail.href?.replace(/^#\//, '') ?? 'overview'); }} />}
        toolsHide
        ariaLabels={{ navigation: '탐색', navigationClose: '탐색 닫기', navigationToggle: '탐색 열기' }}
        content={<>{route.page !== 'detail' && listError && <Alert type="error" header="조회 실패">{listError}</Alert>}{content}</>}
      />
    </div>
  );
}
