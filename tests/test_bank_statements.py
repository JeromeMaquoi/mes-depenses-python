import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bank_statements import REQUIRED_FIELDS, StatementError, categories, output_row, read_statements


def row(**overrides):
    result = {
        "Nº de séquence": "2024-00001", "Date d'exécution": "01/07/2024",
        "Montant": "-12,46", "Devise du compte": "EUR", "Numéro de compte": "BE00TEST",
        "Type de transaction": "Paiement par carte", "Nom de la contrepartie": "",
        "Communication": "", "Détails": "VISA DEBIT - SANS CONTACT", "Statut": "Accepté",
    }
    result.update(overrides)
    return result


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "statement.csv"

    def write(self, rows):
        with self.path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=REQUIRED_FIELDS, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

    def test_debit_not_credit_and_details_preserved(self):
        self.write([row(**{"Détails": "COFEO; VISA DEBIT - SANS CONTACT"})])
        transaction = read_statements([self.path])[0]
        self.assertEqual(categories(transaction), ("Alimentation", "Machine"))
        self.assertEqual(output_row(transaction)["Montant"], "-12.46")
        self.assertEqual(output_row(transaction)["Date"], "01/07/2024")
        self.assertIn("COFEO; VISA DEBIT", output_row(transaction)["Notes"])

    def test_credit_transfer_pharmacy_and_unknown(self):
        rows = [
            row(**{"Type de transaction": "Paiement par carte de crédit"}),
            row(**{"Nº de séquence": "2", "Type de transaction": "Retrait d'espèces par carte"}),
            row(**{"Nº de séquence": "3", "Type de transaction": "Virement en euros", "Communication": "Epargne juillet"}),
            row(**{"Nº de séquence": "4", "Détails": "PHARMACIE"}),
            row(**{"Nº de séquence": "5"}),
        ]
        self.write(rows)
        self.assertEqual([categories(item) for item in read_statements([self.path])], [
            ("Dépenses financières", "Carte de crédit"), ("[Portefeuille]", ""),
            ("[Compte épargne]", ""), ("Santé", "Pharmacie"), ("", ""),
        ])

    def test_duplicate_and_conflict(self):
        self.write([row(), row()])
        self.assertEqual(len(read_statements([self.path, self.path])), 1)
        self.write([row(), row(Montant="-13,00")])
        with self.assertRaisesRegex(StatementError, "conflicting"):
            read_statements([self.path])

    def test_invalid_amount_and_status(self):
        for invalid in (row(Montant="1.00"), row(Statut="Refusé")):
            self.write([invalid])
            with self.assertRaises(StatementError):
                read_statements([self.path])

    def test_short_keywords_do_not_match_inside_words(self):
        self.write([row(**{"Détails": "ARCHITECTURE VISA DEBIT"})])
        self.assertEqual(categories(read_statements([self.path])[0]), ("", ""))

    def test_extracts_card_merchant_only_from_known_format(self):
        details = "PAIEMENT AVEC LA CARTE DE DEBIT NUMERO 1234 56XX XXXX 7890 EXAMPLE SHOP TOWN 01/07/2024 BANCONTACT"
        self.write([row(**{"Détails": details})])
        converted = output_row(read_statements([self.path])[0])
        self.assertEqual(converted["Tiers"], "EXAMPLE SHOP TOWN")
        self.assertEqual(converted["Notes"], details)
        self.write([row(**{"Détails": "UNKNOWN CARD FORMAT"})])
        self.assertEqual(output_row(read_statements([self.path])[0])["Tiers"], "")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.statement = self.root / "statement.csv"
        self.other = self.root / "overlap.csv"
        self.database = self.root / "ledger.sqlite3"
        self.output = self.root / "batch.csv"
        self.script = Path(__file__).resolve().parents[1] / "bank_statements.py"
        self.rows = [row(), row(**{"Nº de séquence": "2", "Montant": "15,50", "Détails": "LOYER"})]
        self.write(self.statement, self.rows)
        self.write(self.other, self.rows[1:])

    def write(self, path, rows):
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=REQUIRED_FIELDS, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

    def command(self, *args, succeeds=True):
        result = subprocess.run(
            [sys.executable, str(self.script), "--state", str(self.database), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8",
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def export(self, output=None):
        self.command("export", self.statement, self.other, "--account", "BE00TEST", "--output", output or self.output)
        manifest_path = Path(str(output or self.output) + ".manifest.json")
        return json.loads(manifest_path.read_text(encoding="utf-8"))["batch_id"]

    def test_existing_export_confirm_and_overlap(self):
        review = self.command("review", self.statement)
        self.assertIn("2024-00001", review.stdout)
        self.command("existing", self.statement, "--account", "BE00TEST", "--id", "2024-00001", "--yes")
        batch_id = self.export()
        with self.output.open(encoding="utf-8", newline="") as output:
            exported = list(csv.DictReader(output, delimiter=";"))
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["Numéro de référence"], "2")
        self.assertEqual(exported[0]["Montant"], "15.50")
        self.assertEqual(exported[0]["Catégorie 1"], "Logement")
        pending = self.command("export", self.statement, "--account", "BE00TEST", "--output", self.root / "other.csv", succeeds=False)
        self.assertIn("pending", pending.stderr)
        self.assertIn("pending", self.command("review", self.statement).stdout)
        self.command("confirm", batch_id, "--yes")
        empty_path = self.root / "empty.csv"
        result = self.command("export", self.other, "--account", "BE00TEST", "--output", empty_path)
        self.assertIn("No new transactions", result.stdout)
        self.assertFalse(empty_path.exists())
        self.assertIn("imported", self.command("review", self.statement).stdout)

    def test_tampering_missing_files_and_discard(self):
        batch_id = self.export()
        manifest = Path(str(self.output) + ".manifest.json")
        original = manifest.read_bytes()
        manifest.write_bytes(b"changed")
        self.assertIn("manifest was modified", self.command("confirm", batch_id, "--yes", succeeds=False).stderr)
        manifest.write_bytes(original)
        original_csv = self.output.read_bytes()
        self.output.write_bytes(b"changed")
        self.assertIn("CSV was modified", self.command("confirm", batch_id, "--yes", succeeds=False).stderr)
        self.output.unlink()
        self.assertIn("unavailable", self.command("confirm", batch_id, "--yes", succeeds=False).stderr)
        self.output.write_bytes(original_csv)
        self.command("discard", batch_id, "--yes")
        new_output = self.root / "new.csv"
        new_id = self.export(new_output)
        self.command("confirm", new_id, "--yes")

    def test_conflicts_and_invalid_input_do_not_create_output(self):
        self.command("existing", self.statement, "--account", "BE00TEST", "--id", "2", "--yes")
        self.write(self.other, [row(**{"Nº de séquence": "2", "Montant": "9,00"})])
        result = self.command("export", self.other, "--account", "BE00TEST", "--output", self.output, succeeds=False)
        self.assertIn("differs from the ledger", result.stderr)
        self.assertFalse(self.output.exists())
        self.write(self.other, [row(Montant="invalid")])
        self.command("export", self.other, "--account", "BE00TEST", "--output", self.output, succeeds=False)
        self.assertFalse(self.output.exists())

    def test_existing_output_and_unknown_ids_are_rejected(self):
        self.output.write_text("leave this alone", encoding="utf-8")
        self.command("export", self.statement, "--account", "BE00TEST", "--output", self.output, succeeds=False)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "leave this alone")
        self.command("existing", self.statement, "--account", "BE00TEST", "--id", "unknown", "--yes", succeeds=False)
        self.assertIn("new", self.command("review", self.statement).stdout)


if __name__ == "__main__":
    unittest.main()