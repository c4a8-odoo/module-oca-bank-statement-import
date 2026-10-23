To pull historical bank statements:

1.  Go to *Invoicing \> Configuration \> Online Bank Statement Providers*
2.  Open the Moss provider
3.  Click *Pull Online Bank Statement*
4.  Configure date interval and click *Pull*

Statement lines are dated by the booking date of the card transaction in
Moss (falling back to the settlement date and the transaction time).

## Currencies

Moss provides every card transaction in the card currency and in the home
currency of the organisation. The journal currency (or the company currency
if the journal has none) must be one of them, usually the home currency.

For transactions in another currency than the journal currency:

- If the currency is available in Odoo, the original amount is kept as
  foreign currency amount on the statement line.
- If the currency is not available in Odoo, the original amount and
  currency are added to the label and the notes of the line. Activate the
  currency in Odoo if you want future lines to carry it as foreign
  currency amount.
