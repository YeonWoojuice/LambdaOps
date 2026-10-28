-- Supports service AND environment filters, not every possible search pattern.
CREATE INDEX ASYNC idx_incidents_canonical_service_env_detected
    ON public.incidents (service, environment, detected_at, incident_id)
    WHERE redirect_to_incident_id IS NULL;
