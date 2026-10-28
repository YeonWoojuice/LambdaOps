-- ID/created_at immutability, ALARM anchors, canonical targets and cycle prevention
-- remain ingestion-service invariants, protected by one per-Alarm transaction.
CREATE TABLE public.incidents (
    incident_id VARCHAR(36) NOT NULL,
    alarm_id VARCHAR(70) NOT NULL,
    anchor_event_key VARCHAR(64) NOT NULL,
    service VARCHAR(100) NOT NULL,
    environment VARCHAR(32) NOT NULL,
    detected_at TIMESTAMPTZ(6) NOT NULL,
    alarm_state VARCHAR(20) NOT NULL,
    incident_state VARCHAR(20) NOT NULL DEFAULT 'NEW',
    last_state_transition_at TIMESTAMPTZ(6) NOT NULL,
    alarm_ok_at TIMESTAMPTZ(6) DEFAULT NULL,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    redirect_to_incident_id VARCHAR(36) DEFAULT NULL,

    CONSTRAINT pk_incidents PRIMARY KEY (incident_id),
    CONSTRAINT uq_incidents_alarm_id UNIQUE (alarm_id, incident_id),
    CONSTRAINT fk_incidents_alarm FOREIGN KEY (alarm_id)
        REFERENCES public.alarms (alarm_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_incidents_anchor FOREIGN KEY (alarm_id, anchor_event_key)
        REFERENCES public.alarm_events (alarm_id, dedupe_key)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_incidents_redirect FOREIGN KEY (alarm_id, redirect_to_incident_id)
        REFERENCES public.incidents (alarm_id, incident_id)
        MATCH SIMPLE ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_incidents_id CHECK (incident_id ~ '^inc_[0-9a-f]{32}$'),
    CONSTRAINT ck_incidents_service CHECK (btrim(service) <> ''),
    CONSTRAINT ck_incidents_environment CHECK (btrim(environment) <> ''),
    CONSTRAINT ck_incidents_alarm_state CHECK (
        alarm_state IN ('ALARM', 'OK', 'INSUFFICIENT_DATA')
    ),
    CONSTRAINT ck_incidents_workflow_state CHECK (incident_state = 'NEW'),
    CONSTRAINT ck_incidents_time_order CHECK (last_state_transition_at >= detected_at),
    -- Separate presence and equality checks: CHECK also accepts SQL UNKNOWN.
    CONSTRAINT ck_incidents_ok_presence CHECK (
        (alarm_state = 'OK') = (alarm_ok_at IS NOT NULL)
    ),
    CONSTRAINT ck_incidents_ok_time CHECK (
        alarm_ok_at IS NULL OR alarm_ok_at = last_state_transition_at
    ),
    CONSTRAINT ck_incidents_redirect_self CHECK (
        redirect_to_incident_id IS NULL OR redirect_to_incident_id <> incident_id
    )
);
