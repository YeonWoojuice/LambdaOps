-- Immutable evidence. The ingestion adapter must validate the envelope and hashes.
-- The previous transition need not have arrived, so it deliberately has no FK.
CREATE TABLE public.alarm_events (
    dedupe_key VARCHAR(64) NOT NULL,
    alarm_id VARCHAR(70) NOT NULL,
    eventbridge_event_id TEXT NOT NULL,
    state VARCHAR(20) NOT NULL,
    transitioned_at TIMESTAMPTZ(6) NOT NULL,
    previous_state VARCHAR(20) NOT NULL,
    previous_transitioned_at TIMESTAMPTZ(6) NOT NULL,
    service VARCHAR(100) NOT NULL,
    environment VARCHAR(32) NOT NULL,
    raw_event JSONB NOT NULL,
    received_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_alarm_events PRIMARY KEY (dedupe_key),
    CONSTRAINT uq_alarm_events_alarm_key UNIQUE (alarm_id, dedupe_key),
    CONSTRAINT uq_alarm_events_alarm_transition UNIQUE (alarm_id, transitioned_at),
    CONSTRAINT fk_alarm_events_alarm FOREIGN KEY (alarm_id)
        REFERENCES public.alarms (alarm_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_alarm_events_key CHECK (dedupe_key ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_alarm_events_event_id CHECK (btrim(eventbridge_event_id) <> ''),
    CONSTRAINT ck_alarm_events_state CHECK (
        state IN ('ALARM', 'OK', 'INSUFFICIENT_DATA')
    ),
    CONSTRAINT ck_alarm_events_previous_state CHECK (
        previous_state IN ('ALARM', 'OK', 'INSUFFICIENT_DATA')
    ),
    CONSTRAINT ck_alarm_events_previous_time CHECK (
        previous_transitioned_at < transitioned_at
    ),
    CONSTRAINT ck_alarm_events_service CHECK (btrim(service) <> ''),
    CONSTRAINT ck_alarm_events_environment CHECK (btrim(environment) <> ''),
    CONSTRAINT ck_alarm_events_raw_object CHECK (jsonb_typeof(raw_event) = 'object')
);
