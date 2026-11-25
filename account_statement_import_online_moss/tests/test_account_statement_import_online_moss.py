# Copyright 2026 glueckkanja AG
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from datetime import date, datetime
from unittest import mock

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

_module_ns = "odoo.addons.account_statement_import_online_moss"
_provider_module = _module_ns + ".models.online_bank_statement_provider"
_provider_class = _provider_module + ".OnlineBankStatementProvider"

EXPENSE_ID = "11111111-1111-4111-8111-111111111111"
REFUND_ID = "22222222-2222-4222-8222-222222222222"


def _response(status_code=200, json_data=None, headers=None, text=""):
    response = mock.Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.text = text
    if json_data is None:
        response.json.side_effect = ValueError("No JSON")
    else:
        response.json.return_value = json_data
    return response


def _page(expenses, page=1, has_more=False):
    return {
        "data": expenses,
        "meta": {
            "apiVersion": "1.50.0",
            "tracingId": "026cb8891f2f1bc257f6876ce4878a3f",
            "pagination": {
                "type": "offset",
                "page": page,
                "pageSize": 100,
                "hasMore": has_more,
            },
        },
    }


class TestAccountStatementImportOnlineMoss(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.now = fields.Datetime.now()
        cls.company_currency = cls.env.company.currency_id
        cls.suspense_account = cls.env["account.account"].create(
            {
                "name": "Bank Suspense Account",
                "code": "1014020",
                "account_type": "asset_current",
            }
        )
        # The journal has no currency on purpose: the company currency is used.
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Moss",
                "type": "bank",
                "code": "MOSS",
                "bank_statements_source": "online",
                "online_bank_statement_provider": "moss",
                "suspense_account_id": cls.suspense_account.id,
            }
        )
        cls.provider = cls.journal.online_bank_statement_provider_id
        cls.provider.write(
            {
                "username": "kid_test",
                "password": "sk_test",
                "statement_creation_mode": "monthly",
                "moss_date_margin": 30,
            }
        )

    def _money(self, amount, currency=None):
        return {"amount": amount, "currency": currency or self.company_currency.name}

    def _expense(self, **values):
        """Card transaction expense as returned by GET /v1/expenses."""
        expense = {
            "id": EXPENSE_ID,
            "organisationId": "47aa12f6-acf9-455f-9f3b-400e5c7a4862",
            "createdBy": "e5f6a7b8-1234-5678-9abc-def012345678",
            "expenseType": "CARD_TRANSACTION",
            "expenseSubType": "EXPENSE",
            "status": "APPROVED",
            "bookingText": "Team coffee",
            "description": None,
            "createTime": "2026-09-02T09:15:00Z",
            "updateTime": "2026-09-05T14:45:00Z",
            "deleteTime": None,
            "expenseTime": "2026-09-01T09:00:00Z",
            "amount": self._money("42.50"),
            "homeAmount": self._money("42.50"),
            "expenseMetadata": {
                "type": "CARD_TRANSACTION",
                "cardId": "c1c1c1c1-c1c1-4c1c-8c1c-c1c1c1c1c1c1",
                "userTransactionTime": "2026-09-01T08:55:00Z",
                "transactionStatus": "ACCEPTED",
                "merchantDetails": {
                    "name": "Coffee Shop Berlin",
                    "address": "123 Main Street",
                    "city": "Berlin",
                    "postalCode": "10115",
                    "country": "DE",
                },
                "settlementDate": "2026-09-03",
                "bookingDate": "2026-09-02",
                "receiptStatus": "FILE_ADDED",
                "invoiceNumber": "INV-2026-001",
            },
            "exportMetadata": {"exportStatus": "NOT_EXPORTED"},
            "lines": [],
        }
        expense.update(values)
        return expense

    def _refund(self, **values):
        metadata = self._expense()["expenseMetadata"]
        metadata.update(
            {
                "bookingDate": "2026-09-10",
                "settlementDate": "2026-09-10",
                "userTransactionTime": "2026-09-09T16:00:00Z",
                "invoiceNumber": None,
                "receiptStatus": "NOT_CREATED",
            }
        )
        refund = self._expense(
            id=REFUND_ID,
            expenseSubType="REFUND",
            bookingText=None,
            description="Returned goods",
            expenseTime="2026-09-09T16:00:00Z",
            amount=self._money("10.00"),
            homeAmount=self._money("10.00"),
            expenseMetadata=metadata,
        )
        refund.update(values)
        return refund

    def _mock_request(self, responses):
        """Mock _moss_request, returning the given responses one by one."""
        return mock.patch(
            _provider_class + "._moss_request",
            side_effect=responses,
        )

    def _obtain(self, expenses):
        with self._mock_request([_page(expenses)]):
            return self.provider._obtain_statement_data(
                datetime(2026, 9, 1), datetime(2026, 10, 1)
            )

    def test_obtain_statement_data(self):
        with self._mock_request([_page([self._expense(), self._refund()])]) as rq:
            lines, statement_values = self.provider._obtain_statement_data(
                datetime(2026, 9, 1), datetime(2026, 10, 1)
            )
        rq.assert_called_once_with(
            "v1/expenses",
            params={
                "type": ["CARD_TRANSACTION"],
                "expense_date__gte": "2026-08-02",
                "expense_date__lte": "2026-10-30",
                "sort": "+expense_time",
                "page": 1,
                "page_size": 100,
            },
        )
        self.assertEqual(statement_values, {})
        self.assertEqual(len(lines), 2)
        purchase, refund = lines
        self.assertEqual(purchase["sequence"], 1)
        self.assertEqual(purchase["date"], datetime(2026, 9, 2, 12, 0))
        self.assertEqual(purchase["amount"], -42.50)
        self.assertNotIn("foreign_currency_id", purchase)
        self.assertEqual(purchase["payment_ref"], "Coffee Shop Berlin - Team coffee")
        self.assertEqual(purchase["ref"], "INV-2026-001")
        self.assertEqual(purchase["partner_name"], "Coffee Shop Berlin")
        self.assertEqual(purchase["transaction_type"], "EXPENSE")
        self.assertEqual(purchase["unique_import_id"], EXPENSE_ID)
        self.assertIn("Moss expense: " + EXPENSE_ID, purchase["narration"])
        self.assertIn("Booking date: 2026-09-02", purchase["narration"])
        self.assertIn(
            "Merchant: Coffee Shop Berlin, 123 Main Street, 10115, Berlin, DE",
            purchase["narration"],
        )
        self.assertIn("Receipt: FILE_ADDED", purchase["narration"])
        self.assertEqual(refund["sequence"], 2)
        self.assertEqual(refund["date"], datetime(2026, 9, 10, 12, 0))
        self.assertEqual(refund["amount"], 10.0)
        self.assertEqual(refund["payment_ref"], "Coffee Shop Berlin - Returned goods")
        self.assertFalse(refund["ref"])
        self.assertEqual(refund["transaction_type"], "REFUND")

    def test_sign_independent_of_api_sign(self):
        """Purchases are negative and refunds positive whatever Moss returns."""
        lines, _ = self._obtain(
            [
                self._expense(
                    amount=self._money("-42.50"), homeAmount=self._money("-42.50")
                ),
                self._refund(
                    amount=self._money("-10.00"), homeAmount=self._money("-10.00")
                ),
                self._refund(
                    id="33333333-3333-4333-8333-333333333333",
                    expenseSubType="CASHBACK",
                    amount=self._money("1.00"),
                    homeAmount=self._money("1.00"),
                ),
            ]
        )
        self.assertEqual([line["amount"] for line in lines], [-42.5, 10.0, 1.0])

    def test_date_margin_and_fallbacks(self):
        self.provider.moss_date_margin = 0
        metadata = self._expense()["expenseMetadata"]
        metadata.update({"bookingDate": None, "settlementDate": None})
        with self._mock_request(
            [_page([self._expense(expenseMetadata=metadata)])]
        ) as rq:
            lines, _ = self.provider._obtain_statement_data(
                datetime(2026, 9, 1), datetime(2026, 10, 1)
            )
        params = rq.call_args.kwargs["params"]
        self.assertEqual(params["expense_date__gte"], "2026-09-01")
        self.assertEqual(params["expense_date__lte"], "2026-09-30")
        # Falls back to the user transaction time.
        self.assertEqual(lines[0]["date"], datetime(2026, 9, 1, 12, 0))
        metadata.update({"userTransactionTime": None})
        lines, _ = self._obtain([self._expense(expenseMetadata=metadata)])
        # Falls back to the expense time.
        self.assertEqual(lines[0]["date"], datetime(2026, 9, 1, 12, 0))

    def test_skip_not_importable(self):
        pending_metadata = self._expense()["expenseMetadata"]
        pending_metadata["transactionStatus"] = "PENDING"
        reversed_metadata = self._expense()["expenseMetadata"]
        reversed_metadata["transactionStatus"] = "REVERSED"
        lines, _ = self._obtain(
            [
                self._expense(
                    id="44444444-4444-4444-8444-444444444444",
                    expenseMetadata=pending_metadata,
                ),
                self._expense(
                    id="55555555-5555-4555-8555-555555555555",
                    expenseMetadata=reversed_metadata,
                ),
                self._expense(
                    id="66666666-6666-4666-8666-666666666666", status="DELETED"
                ),
                self._expense(
                    id="77777777-7777-4777-8777-777777777777",
                    deleteTime="2026-09-05T00:00:00Z",
                ),
                self._expense(
                    id="88888888-8888-4888-8888-888888888888",
                    expenseType="INVOICE",
                ),
                self._expense(),
            ]
        )
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["unique_import_id"], EXPENSE_ID)
        self.assertEqual(lines[0]["sequence"], 1)

    def test_obtain_statement_data_paginated(self):
        third = self._expense(id="33333333-3333-4333-8333-333333333333")
        responses = [
            _page([self._expense(), self._refund()], page=1, has_more=True),
            _page([third], page=2, has_more=False),
        ]
        with self._mock_request(responses) as rq:
            lines, _ = self.provider._obtain_statement_data(
                datetime(2026, 9, 1), datetime(2026, 10, 1)
            )
        self.assertEqual(len(lines), 3)
        self.assertEqual(rq.call_count, 2)
        pages = [call.kwargs["params"]["page"] for call in rq.call_args_list]
        self.assertEqual(pages, [1, 2])

    def test_pull_creates_statement(self):
        with self._mock_request([_page([self._expense(), self._refund()])]):
            self.provider._pull(datetime(2026, 9, 1), datetime(2026, 10, 1))
        statement = self.env["account.bank.statement"].search(
            [("journal_id", "=", self.journal.id)]
        )
        self.assertEqual(len(statement), 1)
        self.assertEqual(statement.name, "MOSS/2026-09-01")
        self.assertEqual(len(statement.line_ids), 2)
        purchase = statement.line_ids.filtered(lambda line: line.amount < 0)
        self.assertEqual(purchase.date, date(2026, 9, 2))
        self.assertEqual(purchase.amount, -42.50)
        self.assertEqual(purchase.payment_ref, "Coffee Shop Berlin - Team coffee")
        self.assertEqual(purchase.partner_name, "Coffee Shop Berlin")
        self.assertIn("Booking date: 2026-09-02", purchase.narration)
        # Pulling again must not duplicate the lines.
        with self._mock_request([_page([self._expense(), self._refund()])]):
            self.provider._pull(datetime(2026, 9, 1), datetime(2026, 10, 1))
        self.assertEqual(len(statement.line_ids), 2)

    def test_foreign_currency_available(self):
        currency = self.env["res.currency"].create({"name": "MOS", "symbol": "M"})
        expense = self._expense(
            amount=self._money("-85.00", "MOS"),
            homeAmount=self._money("42.50"),
            lines=[
                {
                    "id": "f1f1f1f1-f1f1-4f1f-8f1f-f1f1f1f1f1f1",
                    "expenseType": "FX_FEE",
                    "amount": self._money("1.70", "MOS"),
                    "homeAmount": self._money("0.85"),
                }
            ],
        )
        lines, _ = self._obtain([expense])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["amount"], -42.5)
        self.assertEqual(lines[0]["foreign_currency_id"], currency.id)
        self.assertEqual(lines[0]["amount_currency"], -85.0)
        self.assertEqual(lines[0]["payment_ref"], "Coffee Shop Berlin - Team coffee")
        self.assertIn("Amount: -85.00 MOS", lines[0]["narration"])
        self.assertIn(
            f"Home amount: 42.50 {self.company_currency.name}", lines[0]["narration"]
        )
        self.assertIn(
            f"FX fee: 0.85 {self.company_currency.name}", lines[0]["narration"]
        )

    def test_foreign_currency_not_available(self):
        self.env.ref("base.CHF").active = False
        expense = self._expense(
            amount=self._money("40.00", "CHF"), homeAmount=self._money("42.50")
        )
        with self._mock_request([_page([expense])]):
            self.provider._pull(datetime(2026, 9, 1), datetime(2026, 10, 1))
        line = self.env["account.bank.statement.line"].search(
            [("journal_id", "=", self.journal.id)]
        )
        self.assertEqual(len(line), 1)
        self.assertEqual(line.amount, -42.5)
        self.assertFalse(line.foreign_currency_id)
        self.assertEqual(
            line.payment_ref,
            "Coffee Shop Berlin - Team coffee (Original amount 40.00 CHF)",
        )
        self.assertIn("Original amount 40.00 CHF", line.narration)

    def test_journal_in_card_currency(self):
        """The journal may use the card currency instead of the home currency."""
        currency = self.env["res.currency"].create({"name": "MOS", "symbol": "M"})
        self.journal.currency_id = currency
        expense = self._expense(
            amount=self._money("85.00", "MOS"), homeAmount=self._money("42.50")
        )
        lines, _ = self._obtain([expense])
        self.assertEqual(lines[0]["amount"], -85.0)
        self.assertNotIn("foreign_currency_id", lines[0])

    def test_currency_mismatch(self):
        expense = self._expense(
            amount=self._money("85.00", "MOS"), homeAmount=self._money("40.00", "XXX")
        )
        with (
            self._mock_request([_page([expense])]),
            self.assertRaisesRegex(UserError, "home currency XXX"),
        ):
            self.provider._obtain_statement_data(
                datetime(2026, 9, 1), datetime(2026, 10, 1)
            )

    def test_get_token(self):
        token_data = {
            "access_token": "token-1",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read",
        }
        with mock.patch(_provider_module + ".requests") as requests_mock:
            requests_mock.post.return_value = _response(200, token_data)
            self.assertEqual(self.provider._moss_get_token(), "token-1")
            # A second call reuses the cached token.
            self.assertEqual(self.provider._moss_get_token(), "token-1")
            requests_mock.post.assert_called_once()
            call = requests_mock.post.call_args
            self.assertEqual(
                call.args[0], "https://public-api.getmoss.com/oauth2/token"
            )
            self.assertEqual(
                call.kwargs["data"],
                {
                    "grant_type": "client_credentials",
                    "client_id": "kid_test",
                    "client_secret": "sk_test",
                },
            )
            self.assertTrue(self.provider.moss_token_expiration > self.now)
            # An expired token is replaced.
            self.provider.moss_token_expiration = self.now - relativedelta(minutes=1)
            token_data["access_token"] = "token-2"
            self.assertEqual(self.provider._moss_get_token(), "token-2")
            self.assertEqual(requests_mock.post.call_count, 2)

    def test_get_token_custom_api_base(self):
        self.provider.api_base = "https://public-api.runmoss.run/"
        with mock.patch(_provider_module + ".requests") as requests_mock:
            requests_mock.post.return_value = _response(
                200, {"access_token": "token", "expires_in": 3600}
            )
            self.provider._moss_get_token()
        self.assertEqual(
            requests_mock.post.call_args.args[0],
            "https://public-api.runmoss.run/oauth2/token",
        )

    def test_get_token_missing_credentials(self):
        self.provider.password = False
        with self.assertRaisesRegex(UserError, "Key ID and Secret Key"):
            self.provider._moss_get_token()

    def test_get_token_error(self):
        with mock.patch(_provider_module + ".requests") as requests_mock:
            requests_mock.post.return_value = _response(
                401,
                {"error": "invalid_client", "error_description": "Bad credentials"},
            )
            with self.assertRaisesRegex(UserError, "invalid_client: Bad credentials"):
                self.provider._moss_get_token()
        self.assertFalse(self.provider.moss_token)

    def _mock_requests_with_token(self, response):
        """Mock requests so that the token is cached and API calls return response."""
        self.provider.write(
            {
                "moss_token": "token",
                "moss_token_expiration": self.now + relativedelta(hours=1),
            }
        )
        requests_patch = mock.patch(_provider_module + ".requests")
        requests_mock = requests_patch.start()
        self.addCleanup(requests_patch.stop)
        requests_mock.request.return_value = response
        return requests_mock

    def test_request(self):
        requests_mock = self._mock_requests_with_token(_response(200, _page([])))
        self.assertEqual(
            self.provider._moss_search_card_transactions(
                datetime(2026, 9, 1), datetime(2026, 10, 1)
            ),
            [],
        )
        requests_mock.request.assert_called_once_with(
            "get",
            "https://public-api.getmoss.com/v1/expenses",
            params={
                "type": ["CARD_TRANSACTION"],
                "expense_date__gte": "2026-08-02",
                "expense_date__lte": "2026-10-30",
                "sort": "+expense_time",
                "page": 1,
                "page_size": 100,
            },
            json=None,
            headers={"Accept": "application/json", "Authorization": "Bearer token"},
            timeout=60,
        )

    def test_request_error(self):
        self._mock_requests_with_token(
            _response(
                403,
                {
                    "data": None,
                    "meta": {},
                    "errors": [
                        {
                            "type": "ERR_FORBIDDEN",
                            "title": "Insufficient scope",
                            "detail": "read scope required",
                        }
                    ],
                },
            )
        )
        with self.assertRaisesRegex(
            UserError, r"HTTP 403.*ERR_FORBIDDEN: Insufficient scope \(read scope"
        ):
            self.provider._moss_search_card_transactions(
                datetime(2026, 9, 1), datetime(2026, 10, 1)
            )

    def test_request_error_without_json(self):
        self._mock_requests_with_token(_response(502, text="Bad Gateway"))
        with self.assertRaisesRegex(UserError, "HTTP 502.*Bad Gateway"):
            self.provider._moss_search_card_transactions(
                datetime(2026, 9, 1), datetime(2026, 10, 1)
            )

    def test_request_rate_limit(self):
        self._mock_requests_with_token(
            _response(
                429,
                {"errors": [{"type": "ERR_RATE_LIMIT_EXCEEDED", "title": "Slow down"}]},
                headers={"Retry-After": "17"},
            )
        )
        with self.assertRaisesRegex(UserError, "Retry after 17 seconds"):
            self.provider._moss_search_card_transactions(
                datetime(2026, 9, 1), datetime(2026, 10, 1)
            )
