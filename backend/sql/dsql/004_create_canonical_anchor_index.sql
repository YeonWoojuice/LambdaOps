-- Returns job_id. Wait for success before starting ingestion.
-- Redirect rows may retain the same anchor as their canonical row.
CREATE UNIQUE INDEX ASYNC uq_incidents_canonical_anchor
    ON public.incidents (anchor_event_key)
    WHERE redirect_to_incident_id IS NULL;
