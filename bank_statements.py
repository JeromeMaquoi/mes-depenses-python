import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from contextlib import closing


REQUIRED_FIELDS = (
    "Nº de séquence", "Date d'exécution", "Montant", "Devise du compte",
    "Numéro de compte", "Type de transaction", "Nom de la contrepartie",
    "Communication", "Détails", "Statut",
)
OUTPUT_FIELDS = (
    "Date", "Tiers", "Montant", "Catégorie 1", "Catégorie 2", "Notes",
    "Numéro de référence",
)
CATEGORY_RULES = (
    ("Alimentation", "Sandwich & pâtes", ("pasta del couz", "sam s lunch", "choboulette", "kasmi suad", "panos", "mr wu")),
    ("Alimentation", "Supermarché", ("colruyt", "proxy", "lasagnes")),
    ("Loisirs", "Dons", ("amnesty", "world wide fund", "natagora", "greenpeace")),
    ("Transport", "Trottinette", ("bolt",)),
    ("Transport", "Train", ("sncb",)),
    ("Transport", "Bus", ("tec",)),
    ("Logement", "Loyer", ("loyer",)),
    ("Logement", "Eau", ("s.w.d.e.",)),
    ("Logement", "Gaz & Electricité", ("eneco",)),
    ("Alimentation", "Machine", ("cofeo",)),
    ("Salaire", "Salaire net", ("salaire",)),
    ("Logement", "Internet", ("proximus",)),
    ("Transport", "Cambio", ("cambio",)),
    ("Alimentation", "Fastfood", ("deliveroo", "quick", "fritapapa")),
    ("Santé", "Pharmacie", ("pharmacie",)),
    ("Loisirs", "Équipements", ("decathlon",)),
    ("Loisirs", "Bar", ("barnabeer",)),
)


class StatementError(ValueError):
    pass


@dataclass(frozen=True)
class Transaction:
    account: str
    sequence: str
    date: str
    amount: Decimal
    transaction_type: str
    payee: str
    communication: str
    details: str
    fingerprint: str

    @property
    def key(self):
        return (self.account, self.sequence)


def parse_transaction(row):
    if None in row or any(value is None for value in row.values()) or any(row.get(field) is None for field in REQUIRED_FIELDS):
        raise StatementError("incorrect number of columns")
    values = {field: row[field].strip() for field in REQUIRED_FIELDS}
    account = values["Numéro de compte"].replace(" ", "").upper()
    sequence = values["Nº de séquence"]
    if not account or not sequence:
        raise StatementError("account and sequence are required")
    if values["Statut"].casefold() != "accepté":
        raise StatementError("transaction is not accepted; review the statement")
    if values["Devise du compte"].upper() != "EUR":
        raise StatementError("only EUR statements are supported")
    try:
        date = datetime.strptime(values["Date d'exécution"], "%d/%m/%Y").strftime("%d/%m/%Y")
    except ValueError as exc:
        raise StatementError("invalid execution date") from exc
    amount_text = values["Montant"]
    if not re.fullmatch(r"[+-]?\d+,\d{2}", amount_text):
        raise StatementError("amount must have a comma and two decimal places")
    try:
        amount = Decimal(amount_text.replace(",", "."))
    except InvalidOperation as exc:
        raise StatementError("invalid amount") from exc
    normalized = {key: (value or "").strip() for key, value in row.items()}
    normalized["Numéro de compte"] = account
    fingerprint = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return Transaction(account, sequence, date, amount, values["Type de transaction"],
                       values["Nom de la contrepartie"], values["Communication"],
                       values["Détails"], fingerprint)


def read_statements(paths):
    transactions = {}
    for path in paths:
        path = Path(path)
        try:
            with path.open(encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source, delimiter=";", strict=True)
                if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                    raise StatementError(f"{path}: missing or duplicate headers")
                missing = set(REQUIRED_FIELDS) - set(reader.fieldnames)
                if missing:
                    raise StatementError(f"{path}: missing columns: {', '.join(sorted(missing))}")
                for row in reader:
                    try:
                        transaction = parse_transaction(row)
                    except StatementError as exc:
                        raise StatementError(f"{path}:{reader.line_num}: {exc}") from exc
                    previous = transactions.get(transaction.key)
                    if previous and previous.fingerprint != transaction.fingerprint:
                        raise StatementError(f"{path}:{reader.line_num}: conflicting transaction {transaction.sequence}")
                    transactions[transaction.key] = transaction
        except (OSError, UnicodeError, csv.Error) as exc:
            raise StatementError(f"{path}: {exc}") from exc
    return list(transactions.values())


def categories(transaction):
    kind = transaction.transaction_type.casefold()
    text = " ".join((transaction.payee, transaction.communication, transaction.details)).casefold()
    if "retrait" in kind and "espèces" in kind:
        return "[Portefeuille]", ""
    if transaction.amount < 0 and "virement" in kind and ("epargne" in transaction.communication.casefold() or "épargne" in transaction.communication.casefold()):
        return "[Compte épargne]", ""
    if "carte de crédit" in kind:
        return "Dépenses financières", "Carte de crédit"
    for category, subcategory, keywords in CATEGORY_RULES:
        if any(re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text) for keyword in keywords):
            return category, subcategory
    return "", ""


def output_row(transaction):
    category, subcategory = categories(transaction)
    notes = "\n".join(value for value in (transaction.communication, transaction.details) if value)
    payee = transaction.payee
    if not payee and transaction.transaction_type.casefold() == "paiement par carte":
        match = re.search(
            r"PAIEMENT AVEC LA CARTE DE DEBIT NUMERO\s+(?:\S+\s+){4}(.+?)\s+\d{2}/\d{2}/\d{4}\s+(?:BANCONTACT|VISA DEBIT)",
            transaction.details, re.IGNORECASE,
        )
        if match:
            payee = match.group(1).strip()
    return {
        "Date": transaction.date,
        "Tiers": payee,
        "Montant": f"{transaction.amount:.2f}",
        "Catégorie 1": category,
        "Catégorie 2": subcategory,
        "Notes": notes,
        "Numéro de référence": transaction.sequence,
    }


def default_state_path():
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "mes-depenses-python" / "ledger.sqlite3" if base else Path.home() / ".local" / "share" / "mes-depenses-python" / "ledger.sqlite3"


def open_ledger(path):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS recorded (
            account TEXT NOT NULL, sequence TEXT NOT NULL, fingerprint TEXT NOT NULL,
            reason TEXT NOT NULL, PRIMARY KEY (account, sequence)
        );
        CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY, account TEXT NOT NULL, csv_path TEXT NOT NULL,
            manifest_path TEXT NOT NULL, manifest BLOB NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'discarded'))
        );
        CREATE TABLE IF NOT EXISTS batch_items (
            batch_id TEXT NOT NULL REFERENCES batches(id), account TEXT NOT NULL,
            sequence TEXT NOT NULL, fingerprint TEXT NOT NULL,
            PRIMARY KEY (batch_id, account, sequence)
        );
    """)
    return connection


def check_conflicts(connection, transactions):
    for transaction in transactions:
        previous = connection.execute(
            "SELECT fingerprint FROM recorded WHERE account = ? AND sequence = ?",
            transaction.key,
        ).fetchone()
        if previous and previous[0] != transaction.fingerprint:
            raise StatementError(f"transaction {transaction.sequence} differs from the ledger; review it before proceeding")


def require_no_pending(connection):
    pending = connection.execute("SELECT id FROM batches WHERE status = 'pending'").fetchone()
    if pending:
        raise StatementError(f"batch {pending[0]} is pending; confirm or discard it first")


def account_transactions(transactions, account):
    account = account.replace(" ", "").upper()
    selected = [transaction for transaction in transactions if transaction.account == account]
    if not selected:
        raise StatementError("no transactions found for the specified account")
    return selected


def unrecorded(connection, transactions):
    return [transaction for transaction in transactions if not connection.execute(
        "SELECT 1 FROM recorded WHERE account = ? AND sequence = ?", transaction.key
    ).fetchone()]


def review(connection, transactions):
    check_conflicts(connection, transactions)
    for transaction in transactions:
        recorded = connection.execute(
            "SELECT reason FROM recorded WHERE account = ? AND sequence = ?", transaction.key
        ).fetchone()
        pending = connection.execute(
            "SELECT batches.id FROM batches JOIN batch_items ON batches.id = batch_items.batch_id "
            "WHERE batches.status = 'pending' AND batch_items.account = ? AND batch_items.sequence = ?",
            transaction.key,
        ).fetchone()
        status = recorded[0] if recorded else f"pending:{pending[0]}" if pending else "new"
        fields = output_row(transaction)
        print(f"{transaction.account} | {transaction.sequence} | {transaction.date} | "
              f"{transaction.amount:.2f} EUR | {status} | {fields['Tiers']} | "
              f"{fields['Catégorie 1']} | {fields['Catégorie 2']}")
    print(f"{len(transactions)} transaction(s). Review the original statement for full details.")


def record_existing(connection, transactions, identifiers):
    connection.execute("BEGIN IMMEDIATE")
    require_no_pending(connection)
    check_conflicts(connection, transactions)
    by_id = {transaction.sequence: transaction for transaction in transactions}
    missing = set(identifiers) - set(by_id)
    if missing:
        raise StatementError(f"IDs not found in the selected account: {', '.join(sorted(missing))}")
    for identifier in set(identifiers):
        transaction = by_id[identifier]
        connection.execute(
            "INSERT OR IGNORE INTO recorded (account, sequence, fingerprint, reason) VALUES (?, ?, ?, 'existing')",
            (*transaction.key, transaction.fingerprint),
        )
    connection.commit()
    print(f"Recorded {len(set(identifiers))} reviewed existing ID(s).")


def create_batch(connection, transactions, output_path):
    connection.execute("BEGIN IMMEDIATE")
    require_no_pending(connection)
    check_conflicts(connection, transactions)
    selected = unrecorded(connection, transactions)
    if not selected:
        connection.rollback()
        print("No new transactions to export.")
        return
    output_path = Path(output_path).expanduser().resolve()
    manifest_path = output_path.with_name(output_path.name + ".manifest.json")
    if output_path.exists() or manifest_path.exists():
        raise StatementError("output CSV or manifest already exists; choose a new output path")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=OUTPUT_FIELDS, delimiter=";", lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(output_row(transaction) for transaction in selected)
    content = buffer.getvalue().encode("utf-8")
    batch_id = uuid.uuid4().hex
    manifest = {
        "version": 1, "batch_id": batch_id, "account": selected[0].account,
        "csv_sha256": hashlib.sha256(content).hexdigest(),
        "transactions": [{"sequence": transaction.sequence, "fingerprint": transaction.fingerprint} for transaction in selected],
    }
    manifest_content = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    created = []
    try:
        with output_path.open("xb") as output:
            created.append(output_path)
            output.write(content)
        with manifest_path.open("xb") as output:
            created.append(manifest_path)
            output.write(manifest_content)
        connection.execute(
            "INSERT INTO batches (id, account, csv_path, manifest_path, manifest, status) VALUES (?, ?, ?, ?, ?, 'pending')",
            (batch_id, selected[0].account, str(output_path), str(manifest_path), manifest_content),
        )
        connection.executemany(
            "INSERT INTO batch_items (batch_id, account, sequence, fingerprint) VALUES (?, ?, ?, ?)",
            [(batch_id, *transaction.key, transaction.fingerprint) for transaction in selected],
        )
        connection.commit()
    except Exception:
        connection.rollback()
        for path in created:
            path.unlink(missing_ok=True)
        raise
    print(f"Created pending batch {batch_id} with {len(selected)} transaction(s).")
    print(f"CSV: {output_path}\nManifest: {manifest_path}")
    print("Import the complete CSV, then confirm the batch. Generation alone does not record an import.")


def finish_batch(connection, batch_id, confirm):
    connection.execute("BEGIN IMMEDIATE")
    batch = connection.execute(
        "SELECT csv_path, manifest_path, manifest, status FROM batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if not batch or batch[3] != "pending":
        raise StatementError("batch not found or no longer pending")
    if confirm:
        csv_path, manifest_path, manifest_content, _ = batch
        try:
            if Path(manifest_path).read_bytes() != manifest_content:
                raise StatementError("batch manifest was modified; do not confirm this batch")
            digest = hashlib.sha256(Path(csv_path).read_bytes()).hexdigest()
        except OSError as exc:
            raise StatementError(f"batch file unavailable: {exc}") from exc
        if digest != json.loads(manifest_content)["csv_sha256"]:
            raise StatementError("batch CSV was modified; do not confirm this batch")
        connection.execute(
            "INSERT INTO recorded (account, sequence, fingerprint, reason) "
            "SELECT account, sequence, fingerprint, 'imported' FROM batch_items WHERE batch_id = ?",
            (batch_id,),
        )
    status = "confirmed" if confirm else "discarded"
    connection.execute("UPDATE batches SET status = ? WHERE id = ?", (status, batch_id))
    connection.commit()
    print(f"Batch {batch_id} {status}. CSV and manifest files have not been deleted.")


def build_parser():
    parser = argparse.ArgumentParser(description="Convert bank statements to reviewed MyExpenses import batches.")
    parser.add_argument("--state", type=Path, default=default_state_path(), help="local ledger database path")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("review", "existing", "export"):
        command = commands.add_parser(name)
        command.add_argument("statements", nargs="+", type=Path, help="bank CSV statement(s)")
        command.add_argument("--account", required=name != "review", help="bank account number from the statement")
        if name == "existing":
            command.add_argument("--id", action="append", required=True, help="reviewed sequence already in MyExpenses; repeat for each")
            command.add_argument("--yes", action="store_true", required=True, help="confirm these transactions are already present")
        if name == "export":
            command.add_argument("--output", type=Path, required=True, help="new output CSV path")
    for name in ("confirm", "discard"):
        command = commands.add_parser(name)
        command.add_argument("batch_id")
        command.add_argument("--yes", action="store_true", required=True, help="confirm this action")
    commands.add_parser("status")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        with closing(open_ledger(args.state)) as connection, connection:
            if args.command in ("review", "existing", "export"):
                transactions = read_statements(args.statements)
                if args.account:
                    transactions = account_transactions(transactions, args.account)
                if args.command == "review":
                    review(connection, transactions)
                elif args.command == "existing":
                    record_existing(connection, transactions, args.id)
                else:
                    create_batch(connection, transactions, args.output)
            elif args.command in ("confirm", "discard"):
                finish_batch(connection, args.batch_id, args.command == "confirm")
            else:
                pending = connection.execute("SELECT id, account, csv_path FROM batches WHERE status = 'pending'").fetchone()
                print(f"Pending batch: {pending[0]} | {pending[1]} | {pending[2]}" if pending else "No pending batch.")
        return 0
    except (StatementError, OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
