import type {
  AlarmState,
  AlarmStateHistoryEntry,
  IncidentDetail,
  IncidentListResponse,
  IncidentSummary,
} from "./types";

export type AccessTokenProvider = () => string | null | Promise<string | null>;

export interface IncidentApiClientOptions {
  baseUrl: string;
  tokenProvider: AccessTokenProvider;
  fetchImpl?: typeof fetch;
}

export interface ListIncidentsOptions {
  page?: number;
  pageSize?: number;
  signal?: AbortSignal;
}

export type IncidentApiErrorCode =
  | "missing_token"
  | "invalid_request"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "http_error"
  | "network_error"
  | "invalid_response";

export class IncidentApiError extends Error {
  constructor(
    public readonly code: IncidentApiErrorCode,
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "IncidentApiError";
  }
}

const incidentIdPattern = /^inc_[0-9a-f]{32}$/;
const alarmStates: ReadonlySet<string> = new Set(["ALARM", "OK", "INSUFFICIENT_DATA"]);

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(value: Record<string, unknown>, key: string): string {
  const field = value[key];
  if (typeof field !== "string") {
    throw new IncidentApiError("invalid_response", `Invalid Incident response: ${key}`);
  }
  return field;
}

function integerField(value: Record<string, unknown>, key: string, minimum: number, maximum: number): number {
  const field = value[key];
  if (!Number.isSafeInteger(field) || typeof field !== "number" || field < minimum || field > maximum) {
    throw new IncidentApiError("invalid_response", `Invalid Incident response: ${key}`);
  }
  return field;
}

function alarmStateField(value: Record<string, unknown>, key: string): AlarmState {
  const field = stringField(value, key);
  if (!alarmStates.has(field)) {
    throw new IncidentApiError("invalid_response", `Invalid Incident response: ${key}`);
  }
  return field as AlarmState;
}

function parseSummary(value: unknown): IncidentSummary {
  if (!record(value)) {
    throw new IncidentApiError("invalid_response", "Invalid Incident summary");
  }
  const incidentId = stringField(value, "incidentId");
  if (!incidentIdPattern.test(incidentId)) {
    throw new IncidentApiError("invalid_response", "Invalid Incident response: incidentId");
  }
  const summary: IncidentSummary = {
    incidentId,
    account: stringField(value, "account"),
    region: stringField(value, "region"),
    service: stringField(value, "service"),
    environment: stringField(value, "environment"),
    alarmArn: stringField(value, "alarmArn"),
    alarmName: stringField(value, "alarmName"),
    detectedAt: stringField(value, "detectedAt"),
    alarmState: alarmStateField(value, "alarmState"),
    incidentState: stringField(value, "incidentState"),
    lastStateTransitionAt: stringField(value, "lastStateTransitionAt"),
    originalEventRef: stringField(value, "originalEventRef"),
  };
  if (value.alarmOkAt !== undefined) {
    summary.alarmOkAt = stringField(value, "alarmOkAt");
  }
  return summary;
}

function parseHistoryEntry(value: unknown): AlarmStateHistoryEntry {
  if (!record(value) || !record(value.rawEvent)) {
    throw new IncidentApiError("invalid_response", "Invalid Incident state history");
  }
  return {
    eventId: stringField(value, "eventId"),
    state: alarmStateField(value, "state"),
    transitionedAt: stringField(value, "transitionedAt"),
    rawEvent: value.rawEvent,
  };
}

function parseDetail(value: unknown, requestedId: string): IncidentDetail {
  if (!record(value) || !Array.isArray(value.stateHistory)) {
    throw new IncidentApiError("invalid_response", "Invalid Incident detail");
  }
  const summary = parseSummary(value);
  const requestedIncidentId = stringField(value, "requestedIncidentId");
  if (requestedIncidentId !== requestedId || typeof value.redirected !== "boolean" ||
      value.redirected !== (requestedIncidentId !== summary.incidentId)) {
    throw new IncidentApiError("invalid_response", "Invalid Incident detail ID link");
  }
  return {
    ...summary,
    requestedIncidentId,
    redirected: value.redirected,
    stateHistory: value.stateHistory.map(parseHistoryEntry),
  };
}

function parseList(value: unknown): IncidentListResponse {
  if (!record(value) || !Array.isArray(value.items)) {
    throw new IncidentApiError("invalid_response", "Invalid Incident list");
  }
  const page = integerField(value, "page", 1, 1_000_000);
  const pageSize = integerField(value, "pageSize", 1, 100);
  const totalItems = integerField(value, "totalItems", 0, Number.MAX_SAFE_INTEGER);
  const totalPages = integerField(value, "totalPages", 0, Number.MAX_SAFE_INTEGER);
  const hasNextPage = value.hasNextPage;
  if (typeof hasNextPage !== "boolean" || totalPages !== Math.ceil(totalItems / pageSize) ||
      hasNextPage !== (page < totalPages)) {
    throw new IncidentApiError("invalid_response", "Invalid Incident list pagination");
  }
  return {
    items: value.items.map(parseSummary),
    page,
    pageSize,
    totalItems,
    totalPages,
    hasNextPage,
  };
}

function errorForStatus(status: number): IncidentApiError {
  if (status === 401) return new IncidentApiError("unauthorized", "Authentication required", status);
  if (status === 403) return new IncidentApiError("forbidden", "Incident read access denied", status);
  if (status === 404) return new IncidentApiError("not_found", "Incident not found", status);
  if (status === 400) return new IncidentApiError("invalid_request", "Invalid Incident request", status);
  return new IncidentApiError("http_error", "Incident request failed", status);
}

/** M3 read-only client. Callers supply a valid JWT; this class never stores it. */
export class IncidentApiClient {
  private readonly baseUrl: URL;
  private readonly tokenProvider: AccessTokenProvider;
  private readonly fetchImpl: typeof fetch;

  constructor({ baseUrl, tokenProvider, fetchImpl = fetch }: IncidentApiClientOptions) {
    try {
      this.baseUrl = new URL(baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
    } catch {
      throw new IncidentApiError("invalid_request", "A full API base URL is required");
    }
    if (this.baseUrl.protocol !== "https:" || this.baseUrl.username || this.baseUrl.password) {
      throw new IncidentApiError("invalid_request", "An HTTPS API base URL is required");
    }
    this.tokenProvider = tokenProvider;
    this.fetchImpl = fetchImpl;
  }

  async listIncidents(options: ListIncidentsOptions = {}): Promise<IncidentListResponse> {
    const { page = 1, pageSize = 50, signal } = options;
    if (!Number.isInteger(page) || page < 1 || page > 1_000_000) {
      throw new IncidentApiError("invalid_request", "page must be an integer from 1 to 1000000");
    }
    if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
      throw new IncidentApiError("invalid_request", "pageSize must be an integer from 1 to 100");
    }
    const url = new URL("incidents", this.baseUrl);
    url.searchParams.set("page", String(page));
    url.searchParams.set("pageSize", String(pageSize));
    const result = parseList(await this.getJson(url, signal));
    if (result.page !== page || result.pageSize !== pageSize) {
      throw new IncidentApiError("invalid_response", "Incident list page does not match request");
    }
    return result;
  }

  async getIncident(incidentId: string, signal?: AbortSignal): Promise<IncidentDetail> {
    if (!incidentIdPattern.test(incidentId)) {
      throw new IncidentApiError("invalid_request", "Invalid incidentId");
    }
    const url = new URL(`incidents/${incidentId}`, this.baseUrl);
    return parseDetail(await this.getJson(url, signal), incidentId);
  }

  private async getJson(url: URL, signal?: AbortSignal): Promise<unknown> {
    const token = (await this.tokenProvider())?.trim();
    if (!token) {
      throw new IncidentApiError("missing_token", "A valid access token is required");
    }
    let response: Response;
    try {
      response = await this.fetchImpl(url.toString(), {
        method: "GET",
        headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
        cache: "no-store",
        signal,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new IncidentApiError("network_error", "Could not reach the Incident API");
    }
    if (!response.ok) throw errorForStatus(response.status);
    try {
      return await response.json();
    } catch {
      throw new IncidentApiError("invalid_response", "Incident API returned invalid JSON");
    }
  }
}
