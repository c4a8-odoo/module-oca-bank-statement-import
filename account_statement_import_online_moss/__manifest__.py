# Copyright 2026 glueckkanja AG
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Online Bank Statements: Moss",
    "summary": "Import Moss card and wallet transactions as bank statement lines",
    "version": "19.0.1.0.0",
    "category": "Accounting",
    "website": "https://github.com/OCA/bank-statement-import",
    "author": "glueckkanja AG, Odoo Community Association (OCA)",
    "maintainers": ["CRogos"],
    "license": "AGPL-3",
    "installable": True,
    "depends": ["account_statement_import_online"],
    "data": ["views/online_bank_statement_provider.xml"],
}
