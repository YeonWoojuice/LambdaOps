-- Canonical-only time ordering; incident_id breaks ties in detected_at.
CREATE INDEX ASYNC idx_incidents_canonical_detected
    ON public.incidents (detected_at, incident_id)
    WHERE redirect_to_incident_id IS NULL;
