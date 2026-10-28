import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import type { IncidentDetail } from '../data/types';

type AlarmTimelineProps = {
  incident: IncidentDetail;
};

const descriptions = {
  ALARM: 'Alarm 발생',
  INSUFFICIENT_DATA: '평가 데이터 부족 · 복구 판정 아님',
  OK: '이 Alarm 에피소드 종료 · 서비스 복구 판정 아님',
} as const;

function timeLabel(timestamp: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(timestamp));
}

export default function AlarmTimeline({ incident }: AlarmTimelineProps) {
  const history = [...incident.stateHistory].sort((a, b) => a.transitionedAt.localeCompare(b.transitionedAt));
  const latest = history.at(-1);

  return (
    <Container className="timeline-panel" header={<Header variant="h2" description="상태 전환 이력은 현재 Incident 상세 API에서 조회합니다.">Alarm 상태 이력</Header>}>
      <ol className="state-timeline" aria-label="Alarm 상태 전환 이력">
        {history.map(event => (
          <li key={event.eventId} className={`state-timeline-item state-${event.state.toLowerCase()}`}>
            <div className="state-timeline-heading">
              <strong>{event.state}</strong>
              <time dateTime={event.transitionedAt}>{timeLabel(event.transitionedAt)} KST</time>
            </div>
            <p>{descriptions[event.state]}</p>
          </li>
        ))}
      </ol>
      {latest && (
        <details className="raw-event">
          <summary>최근 원본 EventBridge JSON 보기</summary>
          <pre>{JSON.stringify(latest.rawEvent, null, 2)}</pre>
        </details>
      )}
    </Container>
  );
}
