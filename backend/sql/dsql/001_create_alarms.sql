-- Run each numbered DDL file separately with autocommit enabled.
-- Do not wrap the schema installation in one transaction.
CREATE TABLE public.alarms (
    alarm_id VARCHAR(70) NOT NULL,
    account_id VARCHAR(12) NOT NULL,
    region VARCHAR(32) NOT NULL,
    alarm_arn TEXT NOT NULL,
    alarm_name VARCHAR(255) NOT NULL,
    latest_state VARCHAR(20) DEFAULT NULL,
    latest_transition_at TIMESTAMPTZ(6) DEFAULT NULL,
    revision BIGINT NOT NULL DEFAULT 0,

    CONSTRAINT pk_alarms PRIMARY KEY (alarm_id),
    CONSTRAINT ck_alarms_id CHECK (alarm_id ~ '^ALARM#[0-9a-f]{64}$'),
    CONSTRAINT ck_alarms_account CHECK (account_id ~ '^[0-9]{12}$'),
    CONSTRAINT ck_alarms_region CHECK (btrim(region) <> ''),
    CONSTRAINT ck_alarms_arn CHECK (btrim(alarm_arn) <> ''),
    CONSTRAINT ck_alarms_name CHECK (btrim(alarm_name) <> ''),
    CONSTRAINT ck_alarms_state CHECK (
        latest_state IN ('ALARM', 'OK', 'INSUFFICIENT_DATA')
    ),
    CONSTRAINT ck_alarms_state_time CHECK (
        (latest_state IS NULL) = (latest_transition_at IS NULL)
    ),
    CONSTRAINT ck_alarms_revision CHECK (revision >= 0)
);
