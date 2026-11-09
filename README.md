# Bank statements to MyExpenses

Convert the supported Belgian bank CSV format to a reviewed CSV batch for manual import into MyExpenses on Android. A local ledger tracks transactions you explicitly mark as already present or confirm as imported. Generating a batch does **not** mark it imported.

MyExpenses [requires an Extended or Professional licence or its trial](https://github.com/mtotschnig/MyExpenses/blob/master/product-model/business-rules/csv-import-needs-extended-licence.md) for CSV import. The converter does not access your bank or import into Android automatically.

## Setup

Use Python 3.10 or later; there are no external dependencies. In PowerShell, from the project directory, set `$Python` to your interpreter. For a `uv`-installed Python 3.15 on Windows:

```powershell
$Python = "$env:USERPROFILE\.local\bin\python3.15.exe"
& $Python .\bank_statements.py --help
```

If Python is on your PATH, use `$Python = "python"` instead. Quote paths containing spaces. Examples below use placeholder account numbers and transaction IDs; replace them with values shown by `review`.

## First import: review existing data

Your MyExpenses account may already contain transactions from the statement. **Before exporting, compare every candidate with the app.** The tool cannot detect transactions that were previously imported outside its ledger, and matching a date and amount alone is not always enough.

```powershell
& $Python .\bank_statements.py review "C:\path\statement.csv"
```

The review lists the bank account, sequence ID, date, signed EUR amount, ledger status, payee and suggested categories. Consult the original statement for full details. `new` means only that the local ledger has no record of it, not that it is absent from MyExpenses.

Record each ID you have verified is already in the corresponding MyExpenses account. Repeat `--id` for multiple IDs. This is an explicit assertion, so do not mark uncertain IDs as existing:

```powershell
& $Python .\bank_statements.py existing "C:\path\statement.csv" --account "BE00EXAMPLE" --id "2024-00001" --id "2024-00002" --yes
```

You can review and mark existing transactions across several statements; provide each path to the command. Statements can overlap: identical bank account/sequence pairs are deduplicated and conflicting data stops the operation. Select one bank account per export and review historical transactions again when encountering older statements that were not previously reconciled.

## Create and import a batch

```powershell
& $Python .\bank_statements.py export "C:\path\statement.csv" --account "BE00EXAMPLE" --output "$env:USERPROFILE\Downloads\myexpenses-batch-001.csv"
```

Only transactions absent from the ledger are exported. The command displays a batch ID and creates a CSV plus an adjacent `.manifest.json`; use a new output name each time. It does not overwrite existing files. An empty set produces no batch.

Transfer the CSV to your phone, for example through USB or a trusted file-sharing service. In MyExpenses, import it into the corresponding EUR account, selecting:

- Encoding: UTF-8; separator: semicolon; first row: headers.
- Date format: day/month/four-digit year (`dd/MM/yyyy`). Amounts use a decimal point and a leading minus sign for expenses.
- Map `Date` to Date, `Tiers` to Payee/Payer, `Montant` to Amount, `Catégorie 1` to Category, `Catégorie 2` to Subcategory, `Notes` to Notes and `Numéro de référence` to Reference number. Check the preview and mapping on your installed version; MyExpenses can remember the mapping for subsequent files with the same headers.

Import **every row** of the batch. Verify the app's import result and check amounts, dates, payees and categories before confirming the complete batch on the computer:

```powershell
& $Python .\bank_statements.py confirm BATCH_ID --yes
```

Use the printed batch ID in place of `BATCH_ID`. The original CSV and manifest must still be present and unchanged when confirming. Do not edit the CSV or import a different copy. Confirmation is your assertion that the whole batch was successfully imported; the tool cannot inspect the phone or check what was actually imported.

## Pending batches and recovery

Only one batch can be pending at a time. Find its ID with:

```powershell
& $Python .\bank_statements.py status
```

If **none** of a pending batch was imported, discard it before generating a replacement:

```powershell
& $Python .\bank_statements.py discard BATCH_ID --yes
```

If only part was imported, do not confirm or discard as if nothing happened. First reconcile the app: either remove the newly imported rows and their transfer counterparts, then discard and regenerate; or complete the missing rows and verify that every batch row exists exactly once before confirming. Files are not deleted by confirmation or discard. Keep the original unchanged CSV and manifest until confirmation. If they are missing or modified, confirmation is refused; reconcile the app before discarding and regenerating.

## Categories and transfers

Keyword rules suggest categories; unknown entries remain uncategorized. A card merchant is extracted only from a recognizable bank details format, and the original communication and details are retained in Notes. Review suggestions before relying on them. Credit-card settlements are distinct from Visa debit purchases.

Cash withdrawals and outgoing transfers whose communication mentions savings are suggested as `[Portefeuille]` and `[Compte épargne]`. These names must match your MyExpenses accounts. **Do not assume the CSV creates actual transfers:** the upstream importer can convert transfers to ordinary transactions when the destination is absent from the imported account data, even when an account exists in the app. Check and correct these transactions and their destination balances in MyExpenses after import. A savings keyword is only a guess, not proof of the destination.

The converter accepts UTF-8 (with or without BOM), semicolon-separated statements with the supported French headers, accepted transactions and EUR amounts with comma decimals. Non-accepted transactions, invalid data and conflicting duplicates stop processing for review; no currency conversion is performed.

## Privacy and ledger

By default the SQLite ledger is stored at `%LOCALAPPDATA%\mes-depenses-python\ledger.sqlite3` on Windows, outside the repository. It contains account numbers, sequence IDs, transaction fingerprints and batch file paths. The generated CSV and manifest also contain sensitive information. Keep statements, outputs, manifests and ledger backups private and do not commit them. Back up the ledger when the tool is not running: losing it loses duplicate protection. To choose another ledger, place `--state "C:\private\ledger.sqlite3"` before the command; use the same ledger for every run. Do not use a shared or synchronized ledger from multiple computers simultaneously.

## Tests

```powershell
& $Python -m unittest discover -s tests -v
```

