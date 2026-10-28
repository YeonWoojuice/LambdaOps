-- Finds existing redirects when a canonical representative is consolidated.
CREATE INDEX ASYNC idx_incidents_redirect_target
    ON public.incidents (redirect_to_incident_id)
    WHERE redirect_to_incident_id IS NOT NULL;
