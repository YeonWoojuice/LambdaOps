import { describe, expect, it } from "vitest";
import { demoIncidentDetails, demoIncidents, demoMetricSeries } from "./demo";

describe("demo data preserves Alarm episode meaning", () => {
  it("keeps Incident state NEW after an Alarm OK transition", () => {
    const ended = demoIncidents.filter((incident) => incident.alarmState === "OK");
    expect(ended.length).toBeGreaterThan(0);
    expect(ended.every((incident) => incident.incidentState === "NEW" && !!incident.alarmOkAt)).toBe(true);
  });

  it("does not treat INSUFFICIENT_DATA as recovery", () => {
    const insufficient = demoIncidents.find((incident) => incident.alarmState === "INSUFFICIENT_DATA");
    expect(insufficient).toBeDefined();
    expect(insufficient?.incidentState).toBe("NEW");
    expect(insufficient?.alarmOkAt).toBeUndefined();
    expect(demoIncidentDetails[insufficient!.incidentId].stateHistory.at(-1)?.state).toBe("INSUFFICIENT_DATA");
  });

  it("represents only missing metric buckets as null, not all metrics at the state transition", () => {
    const latency = demoMetricSeries.find((metric) => metric.key === "apiLatency")!;
    const errorRate = demoMetricSeries.find((metric) => metric.key === "errorRate")!;
    expect(latency.points).toHaveLength(61);
    expect(latency.points[40]).toMatchObject({ value: null, quality: "missing" });
    expect(errorRate.points[40].value).not.toBeNull();
    expect(Date.parse(latency.points[1].timestamp) - Date.parse(latency.points[0].timestamp)).toBe(60_000);
  });
});
