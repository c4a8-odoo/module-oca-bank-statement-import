# Copyright 2026 glueckkanja AG
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging
from datetime import datetime, time, timedelta

import requests
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MOSS_API_BASE = "https://public-api.getmoss.com"
MOSS_PAGE_SIZE = 100
# Seconds subtracted from the token lifetime so that a token is renewed
# before it actually expires.
MOSS_TOKEN_SAFETY_MARGIN = 60
REQUESTS_TIMEOUT = 60
# Card transaction statuses that are final and must be imported.
MOSS_IMPORTED_TRANSACTION_STATUSES = ("ACCEPTED",)
# Expense subtypes that return money to the card.
MOSS_CREDIT_SUBTYPES = ("REFUND", "CASHBACK")


class OnlineBankStatementProvider(models.Model):
    _inherit = "online.bank.statement.provider"

    moss_date_margin = fields.Integer(
        string="Moss Date Margin (Days)",
        default=30,
        help="Moss filters card transactions by expense date, which is the "
        "receipt date when a receipt is attached and may differ from the "
        "booking date. Expenses are therefore requested for the pulled period "
        "extended by this number of days on both sides. Transactions outside "
        "the period or already imported are ignored.",
    )
    moss_token = fields.Char(readonly=True, copy=False)
    moss_token_expiration = fields.Datetime(readonly=True, copy=False)

    _moss_date_margin_positive = models.Constraint(
        "CHECK(moss_date_margin IS NULL OR moss_date_margin >= 0)",
        "The Moss date margin cannot be negative.",
    )

    @api.model
    def _get_available_services(self):
        """Include the new service Moss in the online providers."""
        return super()._get_available_services() + [
            ("moss", "Moss"),
        ]

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "moss":
            return super()._obtain_statement_data(date_since, date_until)
        return self._moss_obtain_statement_data(date_since, date_until)

    ########
    # Moss #
    ########

    def _moss_get_url(self, endpoint):
        self.ensure_one()
        base = (self.api_base or MOSS_API_BASE).rstrip("/")
        return f"{base}/{endpoint.lstrip('/')}"

    @api.model
    def _moss_get_error_detail(self, response):
        """Extract a readable error message from a Moss API response."""
        try:
            data = response.json()
        except ValueError:
            return response.text
        if not isinstance(data, dict):
            return response.text
        details = []
        for error in data.get("errors") or []:
            detail = f"{error.get('type')}: {error.get('title')}"
            if error.get("detail"):
                detail += f" ({error['detail']})"
            details.append(detail)
        if not details and data.get("error"):
            detail = str(data["error"])
            if data.get("error_description"):
                detail += f": {data['error_description']}"
            details.append(detail)
        return "; ".join(details) or response.text

    def _moss_get_token(self):
        """Return a valid OAuth 2.0 access token, requesting a new one if needed.

        Moss uses the client credentials flow: the Key ID is the client id and
        the Secret Key is the client secret. Tokens cannot be refreshed, a new
        one is requested once the current one expired.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        if (
            self.moss_token
            and self.moss_token_expiration
            and now < self.moss_token_expiration
        ):
            return self.moss_token
        if not self.username or not self.password:
            raise UserError(
                self.env._("Please fill in the Moss Key ID and Secret Key.")
            )
        response = requests.post(
            self._moss_get_url("oauth2/token"),
            data={
                "grant_type": "client_credentials",
                "client_id": self.username,
                "client_secret": self.password,
            },
            headers={"Accept": "application/json"},
            timeout=REQUESTS_TIMEOUT,
        )
        if response.status_code != 200:
            raise UserError(
                self.env._(
                    "Moss authentication failed (HTTP %(status)s): %(detail)s",
                    status=response.status_code,
                    detail=self._moss_get_error_detail(response),
                )
            )
        data = response.json()
        expires_in = int(data.get("expires_in") or 0) - MOSS_TOKEN_SAFETY_MARGIN
        expiration = now + relativedelta(seconds=max(expires_in, 0))
        self.sudo().write(
            {"moss_token": data["access_token"], "moss_token_expiration": expiration}
        )
        return self.moss_token

    def _moss_request(self, endpoint, method="get", params=None, json_data=None):
        """Perform an authenticated request against the Moss API.

        Isolated so that it can be mocked in tests.
        """
        self.ensure_one()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._moss_get_token()}",
        }
        response = requests.request(
            method,
            self._moss_get_url(endpoint),
            params=params,
            json=json_data,
            headers=headers,
            timeout=REQUESTS_TIMEOUT,
        )
        if response.status_code == 429:
            raise UserError(
                self.env._(
                    "Moss API rate limit exceeded. Retry after %(seconds)s seconds.",
                    seconds=response.headers.get("Retry-After", "?"),
                )
            )
        if response.status_code != 200:
            raise UserError(
                self.env._(
                    "Moss API request to %(endpoint)s failed "
                    "(HTTP %(status)s): %(detail)s",
                    endpoint=endpoint,
                    status=response.status_code,
                    detail=self._moss_get_error_detail(response),
                )
            )
        return response.json()

    def _moss_search_card_transactions(self, date_since, date_until):
        """Return the Moss card transaction expenses around the given period.

        Moss filters by expense date, which for card transactions is the
        receipt date when a receipt exists. The requested period is therefore
        extended by the configured margin on both sides; the base module
        filters the lines by their actual date and drops already imported
        ones.
        """
        self.ensure_one()
        margin = timedelta(days=self.moss_date_margin or 0)
        expense_date_from = (date_since - margin).date()
        expense_date_to = (date_until - timedelta(microseconds=1) + margin).date()
        if expense_date_to < expense_date_from:
            expense_date_to = expense_date_from
        expenses = []
        page = 1
        while True:
            data = self._moss_request(
                "v1/expenses",
                params={
                    "type": ["CARD_TRANSACTION"],
                    "expense_date__gte": expense_date_from.isoformat(),
                    "expense_date__lte": expense_date_to.isoformat(),
                    "sort": "+expense_time",
                    "page": page,
                    "page_size": MOSS_PAGE_SIZE,
                },
            )
            page_expenses = data.get("data") or []
            expenses.extend(page_expenses)
            pagination = (data.get("meta") or {}).get("pagination") or {}
            if not page_expenses or not pagination.get("hasMore"):
                break
            page += 1
        return expenses

    def _moss_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        lines = []
        sequence = 0
        for expense in self._moss_search_card_transactions(date_since, date_until):
            if not self._moss_is_importable(expense):
                continue
            sequence += 1
            lines.append(self._moss_prepare_statement_line(expense, sequence, currency))
        return lines, {}

    @api.model
    def _moss_is_importable(self, expense):
        """Only settled, not deleted card transactions become statement lines."""
        if expense.get("expenseType") != "CARD_TRANSACTION":
            return False
        if expense.get("status") == "DELETED" or expense.get("deleteTime"):
            return False
        metadata = expense.get("expenseMetadata") or {}
        status = metadata.get("transactionStatus")
        return not status or status in MOSS_IMPORTED_TRANSACTION_STATUSES

    @api.model
    def _moss_get_line_date(self, expense):
        """Date of the statement line: booking date of the card transaction.

        Falls back to the settlement date, the time the user initiated the
        transaction and finally the expense time.
        """
        metadata = expense.get("expenseMetadata") or {}
        for value in (
            metadata.get("bookingDate"),
            metadata.get("settlementDate"),
            metadata.get("userTransactionTime"),
            expense.get("expenseTime"),
        ):
            if value:
                return fields.Date.from_string(value[:10])
        raise UserError(
            self.env._(
                "Moss card transaction %s has no date.",
                expense.get("id"),
            )
        )

    @api.model
    def _moss_get_signed_amount(self, expense, money):
        """Return the amount with the sign of the statement line.

        Moss reports expenses as positive values; refunds and cashbacks
        return money to the card.
        """
        amount = abs(float((money or {}).get("amount") or 0.0))
        if expense.get("expenseSubType") in MOSS_CREDIT_SUBTYPES:
            return amount
        return -amount

    def _moss_prepare_statement_line(self, expense, sequence, currency):
        self.ensure_one()
        metadata = expense.get("expenseMetadata") or {}
        merchant = metadata.get("merchantDetails") or {}
        merchant_name = merchant.get("name") or ""
        booking_text = expense.get("bookingText") or ""
        description = expense.get("description") or ""
        subtype = expense.get("expenseSubType") or ""
        payment_ref = " - ".join(
            part for part in (merchant_name, booking_text or description) if part
        )
        payment_ref = payment_ref or subtype or "/"
        line_date = self._moss_get_line_date(expense)
        amount_values, note = self._moss_get_amount_values(expense, currency)
        narration = self._moss_get_narration(expense)
        if note:
            payment_ref = f"{payment_ref} ({note})"
            narration = f"{note}\n{narration}"
        line_values = {
            "sequence": sequence,
            # Moss only provides dates. Use midday so that the timezone
            # conversion done by the base module never moves the line to
            # another day.
            "date": datetime.combine(line_date, time(hour=12)),
            "payment_ref": payment_ref,
            "ref": metadata.get("invoiceNumber") or False,
            "partner_name": merchant_name or False,
            "transaction_type": subtype or False,
            "unique_import_id": expense["id"],
            "narration": narration,
        }
        line_values.update(amount_values)
        return line_values

    def _moss_get_amount_values(self, expense, currency):
        """Return the amount values of a statement line and an optional note.

        Moss provides the amount in the card currency (``amount``) and in the
        home currency of the organisation (``homeAmount``). The journal
        currency must be one of them. When the card currency differs from the
        journal currency, the original amount is kept as foreign currency
        amount if that currency is available in Odoo, otherwise it is only
        mentioned in the label and the notes of the line.
        """
        self.ensure_one()
        home_money = expense.get("homeAmount") or {}
        original_money = expense.get("amount") or {}
        if home_money.get("currency") == currency.name:
            money = home_money
        elif original_money.get("currency") == currency.name:
            money = original_money
        else:
            raise UserError(
                self.env._(
                    "Moss card transaction %(expense_id)s is in %(original)s "
                    "(home currency %(home)s) but journal %(journal)s uses "
                    "%(journal_currency)s.",
                    expense_id=expense.get("id"),
                    original=original_money.get("currency"),
                    home=home_money.get("currency"),
                    journal=self.journal_id.display_name,
                    journal_currency=currency.name,
                )
            )
        values = {"amount": self._moss_get_signed_amount(expense, money)}
        original_currency_code = original_money.get("currency")
        if not original_currency_code or original_currency_code == currency.name:
            return values, False
        original_currency = self.env["res.currency"].search(
            [("name", "=", original_currency_code)], limit=1
        )
        if original_currency:
            values.update(
                {
                    "foreign_currency_id": original_currency.id,
                    "amount_currency": self._moss_get_signed_amount(
                        expense, original_money
                    ),
                }
            )
            return values, False
        note = self.env._(
            "Original amount %(amount)s %(currency)s",
            amount=original_money.get("amount"),
            currency=original_currency_code,
        )
        return values, note

    def _moss_get_narration(self, expense):
        """Override to get different notes."""
        metadata = expense.get("expenseMetadata") or {}
        merchant = metadata.get("merchantDetails") or {}
        original_money = expense.get("amount") or {}
        home_money = expense.get("homeAmount") or {}
        notes = [
            self.env._("Moss expense: %s", expense.get("id")),
            self.env._("Type: %s", expense.get("expenseSubType")),
            self.env._("Status: %s", expense.get("status")),
        ]
        for label, value in (
            (self.env._("Transaction time"), metadata.get("userTransactionTime")),
            (self.env._("Booking date"), metadata.get("bookingDate")),
            (self.env._("Settlement date"), metadata.get("settlementDate")),
            (self.env._("Card"), metadata.get("cardId")),
            (self.env._("Receipt"), metadata.get("receiptStatus")),
            (self.env._("Invoice number"), metadata.get("invoiceNumber")),
        ):
            if value:
                notes.append(f"{label}: {value}")
        merchant_address = ", ".join(
            part
            for part in (
                merchant.get("name"),
                merchant.get("address"),
                merchant.get("postalCode"),
                merchant.get("city"),
                merchant.get("country"),
            )
            if part
        )
        if merchant_address:
            notes.append(self.env._("Merchant: %s", merchant_address))
        if original_money:
            notes.append(
                self.env._(
                    "Amount: %(amount)s %(currency)s",
                    amount=original_money.get("amount"),
                    currency=original_money.get("currency"),
                )
            )
        if home_money and home_money != original_money:
            notes.append(
                self.env._(
                    "Home amount: %(amount)s %(currency)s",
                    amount=home_money.get("amount"),
                    currency=home_money.get("currency"),
                )
            )
        for line in expense.get("lines") or []:
            if line.get("expenseType") != "FX_FEE":
                continue
            fee_money = line.get("homeAmount") or line.get("amount") or {}
            notes.append(
                self.env._(
                    "FX fee: %(amount)s %(currency)s",
                    amount=fee_money.get("amount"),
                    currency=fee_money.get("currency"),
                )
            )
        return "\n".join(notes)
