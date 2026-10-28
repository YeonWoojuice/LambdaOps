import json
import unittest
from unittest.mock import Mock

from lambdaops_m3.read_handler import handle_request


def request(params=None, scope="incidents:read", route="GET /incidents"):
    return {"routeKey": route, "queryStringParameters": params,
            "requestContext": {"authorizer": {"jwt": {"claims": {
                "sub": "test-operator", "scope": scope}}}}}


class DsqlReadHandlerTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock(supports_page_queries=True)

    def test_authentication_and_scope_are_checked_before_database_reads(self):
        for event, status in (({"routeKey": "GET /incidents"}, 401),
                              (request(scope="unrelated"), 403)):
            self.assertEqual(handle_request(event, self.store)["statusCode"], status)
        self.store.list_incidents_page.assert_not_called()
        self.store.get_incident.assert_not_called()

    def test_page_contract_returns_store_metadata(self):
        body = {"items": [], "page": 3, "pageSize": 20,
                "totalItems": 0, "totalPages": 0, "hasNextPage": False}
        self.store.list_incidents_page.return_value = body
        response = handle_request(request({"page": "3", "pageSize": "20"}), self.store)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"]), body)
        self.assertEqual(response["headers"]["cache-control"], "no-store")

    def test_invalid_query_never_reaches_database(self):
        for params in ({"page": "0"}, {"nextToken": "legacy"}, {"sortBy": "drop table"}):
            with self.subTest(params=params):
                self.assertEqual(handle_request(request(params), self.store)["statusCode"], 400)
        self.store.list_incidents_page.assert_not_called()

    def test_storage_failure_is_503_without_database_details(self):
        self.store.list_incidents_page.side_effect = RuntimeError("private database detail")
        response = handle_request(request(), self.store)
        self.assertEqual(response["statusCode"], 503)
        self.assertNotIn("private", response["body"])
        event = request(route="GET /incidents/{incidentId}")
        event["pathParameters"] = {"incidentId": "inc_" + "a" * 32}
        self.store.get_incident.side_effect = RuntimeError("private database detail")
        self.assertEqual(handle_request(event, self.store)["statusCode"], 503)
