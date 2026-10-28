import unittest

from lambdaops_m3.query_contract import IncidentQuery, QueryContractError, parse_incident_query
from lambdaops_m3.dsql_queries import _filters


class QueryContractTests(unittest.TestCase):
    def test_defaults(self):
        query = parse_incident_query({})
        self.assertEqual(query, IncidentQuery())
        self.assertEqual(query.page, 1)
        self.assertEqual(query.page_size, 50)

    def test_none_uses_defaults(self):
        self.assertEqual(parse_incident_query(None), IncidentQuery())

    def test_all_supported_parameters(self):
        query = IncidentQuery.parse({
            "page": "2", "pageSize": "20", "service": "slash-api", "environment": "dev",
            "alarmState": "OK", "incidentState": "NEW", "from": "2026-10-01T00:00:00+09:00",
            "to": "2026-11-01T00:00:00+09:00", "search": "지연", "sortBy": "createdAt",
            "sortOrder": "asc",
        })
        self.assertEqual(query.page, 2)
        self.assertEqual(query.page_size, 20)
        self.assertEqual(query.from_at, "2026-09-30T15:00:00.000000Z")
        self.assertEqual(query.to_at, "2026-10-31T15:00:00.000000Z")
        self.assertEqual(query.alarm_state, "OK")

    def test_page_boundaries(self):
        for page in ("1", "1000000"):
            with self.subTest(page=page):
                self.assertEqual(parse_incident_query({"page": page}).page, int(page))

    def test_bad_pages(self):
        for page in ("0", "-1", "+1", "1.5", " 1", "", "1000001", "999999999999", 1, None, True):
            with self.subTest(page=page), self.assertRaises(QueryContractError):
                parse_incident_query({"page": page})

    def test_page_size_boundaries(self):
        for size in ("1", "100"):
            self.assertEqual(parse_incident_query({"pageSize": size}).page_size, int(size))

    def test_bad_page_sizes(self):
        for size in ("0", "101", "-1", "50.0", "", None):
            with self.subTest(size=size), self.assertRaises(QueryContractError):
                parse_incident_query({"pageSize": size})

    def test_rejects_unknown_and_legacy_parameters(self):
        for name in ("limit", "nextToken", "offset", "sort", "ownerId"):
            with self.subTest(name=name), self.assertRaises(QueryContractError):
                parse_incident_query({name: "1"})

    def test_rejects_non_mapping(self):
        for params in ([], "", 5):
            with self.subTest(params=params), self.assertRaises(QueryContractError):
                parse_incident_query(params)

    def test_text_limits(self):
        for name, maximum in (("service", 100), ("environment", 32), ("search", 200)):
            with self.subTest(name=name):
                parse_incident_query({name: "x" * maximum})
                with self.assertRaises(QueryContractError):
                    parse_incident_query({name: "x" * (maximum + 1)})

    def test_rejects_empty_null_and_blank_text(self):
        for name in ("service", "environment", "search"):
            for value in ("", "\t \n", "\u2003", "nul\x00", None, [], 2):
                with self.subTest(name=name, value=value), self.assertRaises(QueryContractError):
                    parse_incident_query({name: value})

    def test_exact_text_is_not_trimmed(self):
        self.assertEqual(parse_incident_query({"service": " service "}).service, " service ")

    def test_alarm_states(self):
        for state in ("ALARM", "OK", "INSUFFICIENT_DATA"):
            self.assertEqual(parse_incident_query({"alarmState": state}).alarm_state, state)

    def test_invalid_states(self):
        for params in ({"alarmState": "NEW"}, {"alarmState": "alarm"},
                       {"alarmState": "ALARM,OK"}, {"incidentState": "Resolved"},
                       {"incidentState": "OK"}):
            with self.subTest(params=params), self.assertRaises(QueryContractError):
                parse_incident_query(params)

    def test_invalid_dates(self):
        for value in ("2026-10-28", "2026-10-28T00:00:00", "2026-02-30T00:00:00Z",
                      "2026-10-28T00:00:00+0900", "2026-10-28T00:00:00+01:60",
                      "2026-10-28T00:00:00+24:00", "2026-10-28T00:00:00.1234567Z",
                      "0001-01-01T00:00:00+09:00", "bad", None):
            with self.subTest(value=value), self.assertRaises(QueryContractError):
                parse_incident_query({"from": value})

    def test_timestamp_microseconds(self):
        self.assertEqual(parse_incident_query({"from": "2026-10-28T01:02:03.123456Z"}).from_at,
                         "2026-10-28T01:02:03.123456Z")

    def test_date_comparison_uses_instant(self):
        with self.assertRaises(QueryContractError):
            parse_incident_query({"from": "2026-10-28T10:00:00+09:00", "to": "2026-10-28T01:00:00Z"})
        with self.assertRaises(QueryContractError):
            parse_incident_query({"from": "2026-10-29T00:00:00Z", "to": "2026-10-28T00:00:00Z"})

    def test_one_sided_time_range(self):
        self.assertIsNone(parse_incident_query({"from": "2026-10-28T00:00:00Z"}).to_at)
        self.assertIsNone(parse_incident_query({"to": "2026-10-28T00:00:00Z"}).from_at)

    def test_sort_allowlist(self):
        for sort in ("detectedAt", "lastStateTransitionAt", "createdAt"):
            for order in ("asc", "desc"):
                parse_incident_query({"sortBy": sort, "sortOrder": order})
        for params in ({"sortBy": "detected_at"}, {"sortBy": "id; DROP TABLE alarms"},
                       {"sortOrder": "DESC"}, {"sortOrder": "asc NULLS FIRST"},
                       {"sortBy": []}, {"sortOrder": None}):
            with self.subTest(params=params), self.assertRaises(QueryContractError):
                parse_incident_query(params)

    def test_filter_values_are_bound(self):
        injected = "x' OR TRUE --"
        where, values = _filters(parse_incident_query({"service": injected, "search": injected}))
        self.assertNotIn(injected, where)
        self.assertEqual(values[0], injected)
        self.assertIn("i.redirect_to_incident_id IS NULL", where)

    def test_search_treats_wildcards_literally(self):
        where, values = _filters(parse_incident_query({"search": "20%_!"}))
        self.assertEqual(values, ["%20!%!_!!%"] * 4)
        self.assertEqual(where.count("ESCAPE '!'"), 4)

    def test_time_filter_half_open_interval(self):
        where, values = _filters(parse_incident_query({
            "from": "2026-10-01T00:00:00Z", "to": "2026-11-01T00:00:00Z",
        }))
        self.assertIn("i.detected_at >= %s", where)
        self.assertIn("i.detected_at < %s", where)
        self.assertEqual(len(values), 2)


if __name__ == "__main__":
    unittest.main()
