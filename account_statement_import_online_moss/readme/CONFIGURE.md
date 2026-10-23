## On the Moss website

1.  Log in to Moss as an admin.
2.  Go to *Settings \> Platform \> API Keys* and create a new API key.
    Confirm the operation in the Moss mobile app.
3.  Copy both the Key ID (`kid_...`) and the Secret Key (`sk_...`). The
    Secret Key is only shown once.

## On Odoo

To configure online bank statements provider:

1.  Add your user to the "Full Accounting Settings" group.
2.  Go to *Invoicing \> Configuration \> Accounting \> Journals*.
3.  Select the journal representing your Moss cards (or create it, type
    *Bank*). The journal currency, or the company currency if the journal
    has none, must be the home currency of your Moss organisation or the
    currency of your cards.
4.  Set *Bank Feeds* to *Online (OCA)*.
5.  Select *Moss* as online bank statements provider in *Online Bank
    Statements (OCA)* section.
6.  Save the journal and click on the created provider.
7.  Put the Key ID and Secret Key into the corresponding fields.
8.  Optionally adjust the *Moss Date Margin*. Moss filters card
    transactions by expense date, which is the receipt date when a receipt
    is attached. The pulled period is extended by this margin on both
    sides so that such transactions are not missed; already imported
    transactions are never duplicated.
9.  Configure the schedule and the statement creation mode and save.

In developer mode the *API Base URL* field allows to point the provider to
the Moss staging environment (`https://public-api.runmoss.run`).
