# M2 CloudWatch alarms

This module defines the first three Slash alarms. It is **not wired to an environment or deployed**. Choose thresholds from measured traffic before an environment calls the module. The module creates no AWS resources until an environment runs `terraform apply`.

## Input metric contract

The Slash publisher must emit the following five standard-resolution metrics every completed 60-second interval under namespace `LambdaOps/Slash`, with exactly `Service=<service>` and `Environment=<environment>` as dimensions. Each data point summarizes one publish interval; all five should use the same timestamp for that interval. These are interval totals, not per-request points or process-lifetime cumulative counters. Send a zero data point for quiet intervals, including `ServerErrorCount=0`, so a missing point signals a publisher problem rather than normal inactivity. Do not emit user IDs, raw URL paths, SQL, request IDs, `Version`, `Method`, or `Status` as dimensions of these alarm metrics.

| Metric | Unit | Meaning |
| --- | --- | --- |
| `RequestCount` | `Count` | Completed API requests in the interval |
| `ServerErrorCount` | `Count` | Completed API requests whose final status is 5xx |
| `RequestDurationMsTotal` | `Milliseconds` | Sum of completed API request durations in milliseconds |
| `DBQueryCount` | `Count` | Completed instrumented DB queries in the interval |
| `DBQueryDurationMsTotal` | `Milliseconds` | Sum of completed instrumented DB query durations in milliseconds |

The numerator and denominator of each ratio must cover the same service, interval, and request/query population. A DB query metric must be instrumented in the actual AWS profile; the M1 jOOQ timer is currently limited to `local & m1`. This module does not emit metrics. The publisher may also emit `DBConnectionsActive` (`Count`) with the same namespace and dimensions as a diagnostic Hikari gauge. It has no M2 alarm because the first three fault tests cover HTTP 5xx, API latency, and DB query latency. RDS `DatabaseConnections` is a different, instance-wide signal.

## Alarms

Each alarm evaluates 60-second `Sum` values, by default requiring 2 breaching periods out of 3. Missing data is `notBreaching`. The count guard returns zero when an interval has too few samples; both minimum counts are required caller inputs.

| Alarm | Metric math | Caller-supplied threshold |
| --- | --- | --- |
| API 5xx rate | `IF(RequestCount >= minimum_requests_per_period, 100 * ServerErrorCount / RequestCount, 0)` | percent |
| Mean API latency | `IF(RequestCount >= minimum_requests_per_period, RequestDurationMsTotal / RequestCount, 0)` | milliseconds |
| Mean DB query latency | `IF(DBQueryCount >= minimum_db_queries_per_period, DBQueryDurationMsTotal / DBQueryCount, 0)` | milliseconds |

All three thresholds and both minimum counts have no defaults. This prevents treating the presentation's example thresholds as validated production values. These are **mean** latency alarms, not p95 alarms. Because `notBreaching` can hide a stopped publisher, a separate telemetry-absence or availability alarm is still required when deploying the end-to-end service, even though normal quiet intervals emit zeros.

## Integration and verification

Call this module from a separately configured AWS environment after setting its provider, state backend, task runtime, publisher, and required variables. No `alarm_actions` are configured; a later M3 EventBridge rule will receive CloudWatch alarm state changes.

```hcl
module "slash_m2_alarms" {
  source = "../../modules/m2-alarms"

  service                               = "slash-api"
  environment                           = "dev"
  api_5xx_rate_threshold_percent        = var.api_5xx_rate_threshold_percent
  api_average_latency_threshold_ms      = var.api_average_latency_threshold_ms
  db_average_query_latency_threshold_ms = var.db_average_query_latency_threshold_ms
  minimum_requests_per_period           = var.minimum_requests_per_period
  minimum_db_queries_per_period         = var.minimum_db_queries_per_period
}
```

After a reviewed deployment, check source data and alarm state with the *same account, region, namespace, unit, and dimensions* used by the publisher:

```bash
aws cloudwatch get-metric-statistics \
  --namespace LambdaOps/Slash --metric-name RequestCount \
  --dimensions Name=Service,Value=slash-api Name=Environment,Value=dev \
  --statistics Sum --period 60 --start-time START_UTC --end-time END_UTC

aws cloudwatch describe-alarms \
  --alarm-name-prefix lambdaops-dev-slash-api-
```

Repeat a bounded, private 500 response, API delay, and DB query delay test long enough to cross the selected 60-second alarm evaluation periods. Check ALARM and subsequent OK transitions, and confirm the low-volume guard suppresses alerts below the configured minimum. The M1-only fault endpoints must not be exposed on a public AWS route.
