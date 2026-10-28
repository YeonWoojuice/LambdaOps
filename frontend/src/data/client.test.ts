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

  it("sends a JWT only in the header and keeps the page token opaque", async () => {
    let requestedUrl = "";
    let requestedInit: RequestInit | undefined;
    const fetchImpl: typeof fetch = async (url, init) => {
      requestedUrl = String(url);
      requestedInit = init;
      return Response.json({ items: [demoIncidents[0]], nextToken: "next/cursor+==" });
    };
    const client = new IncidentApiClient({
      baseUrl,
      tokenProvider: async () => "secret.jwt",
      fetchImpl,
    });

    const page = await client.listIncidents({ limit: 25, nextToken: "opaque/+=?" });

    expect(page.items[0].incidentId).toBe(demoIncidents[0].incidentId);
    expect(page.nextToken).toBe("next/cursor+==");
    const url = new URL(requestedUrl);
    expect(url.pathname).toBe("/incidents");
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.get("nextToken")).toBe("opaque/+=?");
    expect(requestedUrl).not.toContain("secret.jwt");
    expect(new Headers(requestedInit?.headers).get("Authorization")).toBe("Bearer secret.jwt");
    expect(requestedInit?.cache).toBe("no-store");
  });

  it("reads the detail and its Alarm state history", async () => {
    const fetchImpl: typeof fetch = async () => Response.json(demoIncidentDetail);
    const client = new IncidentApiClient({ baseUrl, tokenProvider: () => "valid.jwt", fetchImpl });

    const detail = await client.getIncident(demoIncidentDetail.incidentId);

    expect(detail.incidentState).toBe("NEW");
    expect(detail.alarmState).toBe("OK");
    expect(detail.stateHistory.map((entry) => entry.state)).toEqual([
      "ALARM", "INSUFFICIENT_DATA", "OK",
    ]);
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

  it("rejects malformed IDs and response shapes", async () => {
    const fetchImpl: typeof fetch = async () => Response.json({ items: [], nextToken: 123 });
    const client = new IncidentApiClient({ baseUrl, tokenProvider: () => "valid.jwt", fetchImpl });

    await expect(client.getIncident("../bad")).rejects.toMatchObject({ code: "invalid_request" });
    await expect(client.listIncidents()).rejects.toMatchObject({ code: "invalid_response" });
    expect(IncidentApiError).toBeDefined();
  });
});
