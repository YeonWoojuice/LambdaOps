import { describe, expect, it, vi } from "vitest";
import { IncidentApiClient, IncidentApiError } from "./client";
import { demoIncidentDetail, demoIncidents } from "./demo";

const baseUrl = "https://example.execute-api.ap-northeast-2.amazonaws.com/";

describe("IncidentApiClient", () => {
  it("rejects an insecure API origin before sending a JWT", () => {
    expect(() => new IncidentApiClient({
      baseUrl: "http://example.execute-api.ap-northeast-2.amazonaws.com/",
      tokenProvider: () => "secret.jwt",
    })).toThrowError(IncidentApiError);
  });

  it("uses the DSQL page contract and sends a JWT only in the header", async () => {
    let requestedUrl = "";
    let requestedInit: RequestInit | undefined;
    const fetchImpl: typeof fetch = async (url, init) => {
      requestedUrl = String(url);
      requestedInit = init;
      return Response.json({
        items: [demoIncidents[0]],
        page: 2,
        pageSize: 25,
        totalItems: 26,
        totalPages: 2,
        hasNextPage: false,
      });
    };
    const client = new IncidentApiClient({
      baseUrl,
      tokenProvider: async () => "secret.jwt",
      fetchImpl,
    });

    const page = await client.listIncidents({ page: 2, pageSize: 25 });

    expect(page.items[0].incidentId).toBe(demoIncidents[0].incidentId);
    expect(page).toMatchObject({ page: 2, pageSize: 25, totalItems: 26, totalPages: 2, hasNextPage: false });
    const url = new URL(requestedUrl);
    expect(url.pathname).toBe("/incidents");
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("pageSize")).toBe("25");
    expect(url.searchParams.has("limit")).toBe(false);
    expect(url.searchParams.has("nextToken")).toBe(false);
    expect(requestedUrl).not.toContain("secret.jwt");
    expect(new Headers(requestedInit?.headers).get("Authorization")).toBe("Bearer secret.jwt");
    expect(requestedInit?.cache).toBe("no-store");
  });

  it("reads the detail and its Alarm state history", async () => {
    const fetchImpl: typeof fetch = async () => Response.json({
      ...demoIncidentDetail,
      requestedIncidentId: demoIncidentDetail.incidentId,
      redirected: false,
    });
    const client = new IncidentApiClient({ baseUrl, tokenProvider: () => "valid.jwt", fetchImpl });

    const detail = await client.getIncident(demoIncidentDetail.incidentId);

    expect(detail.incidentState).toBe("NEW");
    expect(detail.alarmState).toBe("OK");
    expect(detail.requestedIncidentId).toBe(demoIncidentDetail.incidentId);
    expect(detail.redirected).toBe(false);
    expect(detail.stateHistory.map((entry) => entry.state)).toEqual([
      "ALARM", "INSUFFICIENT_DATA", "OK",
    ]);
  });

  it("preserves a requested ID linked to the canonical Incident", async () => {
    const requestedIncidentId = "inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const fetchImpl: typeof fetch = async () => Response.json({
      ...demoIncidentDetail,
      requestedIncidentId,
      redirected: true,
    });
    const client = new IncidentApiClient({ baseUrl, tokenProvider: () => "valid.jwt", fetchImpl });

    const detail = await client.getIncident(requestedIncidentId);

    expect(detail.requestedIncidentId).toBe(requestedIncidentId);
    expect(detail.incidentId).toBe(demoIncidentDetail.incidentId);
    expect(detail.redirected).toBe(true);
  });

  it("rejects missing credentials before any network request", async () => {
    const fetchImpl = vi.fn<typeof fetch>();
    const client = new IncidentApiClient({ baseUrl, tokenProvider: () => null, fetchImpl });

    await expect(client.listIncidents()).rejects.toMatchObject({ code: "missing_token" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it.each([
    [401, "unauthorized"],
    [403, "forbidden"],
    [404, "not_found"],
  ] as const)("maps HTTP %i to %s", async (status, code) => {
    const fetchImpl: typeof fetch = async () => new Response(null, { status });
    const client = new IncidentApiClient({ baseUrl, tokenProvider: () => "valid.jwt", fetchImpl });

    await expect(client.getIncident(demoIncidentDetail.incidentId)).rejects.toMatchObject({ code, status });
  });

  it("rejects malformed IDs, page options, and a legacy list response", async () => {
    const fetchImpl: typeof fetch = async () => Response.json({ items: [], nextToken: null });
    const client = new IncidentApiClient({ baseUrl, tokenProvider: () => "valid.jwt", fetchImpl });

    await expect(client.getIncident("../bad")).rejects.toMatchObject({ code: "invalid_request" });
    await expect(client.listIncidents({ page: 0 })).rejects.toMatchObject({ code: "invalid_request" });
    await expect(client.listIncidents({ pageSize: 101 })).rejects.toMatchObject({ code: "invalid_request" });
    await expect(client.listIncidents()).rejects.toMatchObject({ code: "invalid_response" });
    expect(IncidentApiError).toBeDefined();
  });

  it("rejects inconsistent DSQL pagination and detail ID links", async () => {
    const listFetch: typeof fetch = async () => Response.json({
      items: [], page: 1, pageSize: 50, totalItems: 51, totalPages: 2, hasNextPage: false,
    });
    const listClient = new IncidentApiClient({ baseUrl, tokenProvider: () => "valid.jwt", fetchImpl: listFetch });
    await expect(listClient.listIncidents()).rejects.toMatchObject({ code: "invalid_response" });

    const wrongPageFetch: typeof fetch = async () => Response.json({
      items: [], page: 2, pageSize: 50, totalItems: 0, totalPages: 0, hasNextPage: false,
    });
    const wrongPageClient = new IncidentApiClient({ baseUrl, tokenProvider: () => "valid.jwt", fetchImpl: wrongPageFetch });
    await expect(wrongPageClient.listIncidents()).rejects.toMatchObject({ code: "invalid_response" });

    const detailFetch: typeof fetch = async () => Response.json({
      ...demoIncidentDetail,
      requestedIncidentId: demoIncidentDetail.incidentId,
      redirected: true,
    });
    const detailClient = new IncidentApiClient({ baseUrl, tokenProvider: () => "valid.jwt", fetchImpl: detailFetch });
    await expect(detailClient.getIncident(demoIncidentDetail.incidentId)).rejects.toMatchObject({ code: "invalid_response" });
  });
});
