This module provides online bank statements from [Moss](https://www.getmoss.com).

It pulls the card transactions of your Moss organisation through the
[Moss public API](https://developers.getmoss.com/) (`GET /v1/expenses`) and
imports them as bank statement lines of a credit card journal. Purchases are
imported as negative amounts, refunds and cashbacks as positive amounts.
Only settled (accepted) card transactions are imported, pending ones are
picked up once they settle.

Each statement line carries the merchant as partner name, the amount in the
journal currency and, for purchases in another currency, the original
amount as foreign currency amount. Card, receipt, merchant and FX fee details
are kept in the notes of the line.

The module only reads from Moss. It never changes anything in Moss, in
particular it does not update the export status of expenses.
