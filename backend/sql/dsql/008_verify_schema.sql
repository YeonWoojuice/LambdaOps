-- Read-only verification. Missing indexes must produce false, not disappear
-- from the aggregate. Expect 10 found indexes and all_expected_indexes_valid=t.
WITH expected(index_name, table_name, is_unique) AS (
    VALUES
        ('pk_alarms', 'alarms', true),
        ('pk_alarm_events', 'alarm_events', true),
        ('uq_alarm_events_alarm_key', 'alarm_events', true),
        ('uq_alarm_events_alarm_transition', 'alarm_events', true),
        ('pk_incidents', 'incidents', true),
        ('uq_incidents_alarm_id', 'incidents', true),
        ('uq_incidents_canonical_anchor', 'incidents', true),
        ('idx_incidents_canonical_detected', 'incidents', false),
        ('idx_incidents_canonical_service_env_detected', 'incidents', false),
        ('idx_incidents_redirect_target', 'incidents', false)
)
SELECT
    count(*) AS expected_index_count,
    count(i.indexrelid) AS found_index_count,
    bool_and(coalesce(
        i.indisvalid AND i.indisready
        AND i.indisunique = e.is_unique
        AND t.relname = e.table_name
        AND t.relnamespace = n.oid,
        false
    )) AS all_expected_indexes_valid
FROM expected AS e
LEFT JOIN pg_catalog.pg_namespace AS n ON n.nspname = 'public'
LEFT JOIN pg_catalog.pg_class AS c
    ON c.relnamespace = n.oid AND c.relname = e.index_name
LEFT JOIN pg_catalog.pg_index AS i ON i.indexrelid = c.oid
LEFT JOIN pg_catalog.pg_class AS t ON t.oid = i.indrelid;

-- Compare definitions (columns, order, predicates) with the approved schema.
SELECT
    t.relname AS table_name,
    c.relname AS index_name,
    i.indisvalid,
    i.indisunique,
    pg_catalog.pg_get_indexdef(i.indexrelid) AS index_definition
FROM pg_catalog.pg_index AS i
JOIN pg_catalog.pg_class AS c ON c.oid = i.indexrelid
JOIN pg_catalog.pg_class AS t ON t.oid = i.indrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND t.relname IN ('alarms', 'alarm_events', 'incidents')
ORDER BY t.relname, c.relname;

-- Expect 8 alarms columns, 11 alarm_events columns and 13 incidents columns.
SELECT table_name, ordinal_position, column_name, data_type,
       character_maximum_length, datetime_precision, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('alarms', 'alarm_events', 'incidents')
ORDER BY table_name, ordinal_position;

SELECT t.relname AS table_name, c.conname AS constraint_name,
       c.contype AS constraint_type,
       pg_catalog.pg_get_constraintdef(c.oid) AS constraint_definition
FROM pg_catalog.pg_constraint AS c
JOIN pg_catalog.pg_class AS t ON t.oid = c.conrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND t.relname IN ('alarms', 'alarm_events', 'incidents')
ORDER BY t.relname, c.conname;
