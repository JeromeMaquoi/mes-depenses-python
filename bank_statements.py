import csv
from datetime import datetime
import os

# Define the input and output file paths
input_file = r'D:\Users\jerom\Documents\Nextcloud\Documents importants\Banque\Extraits de compte\CSV_2024-11-12-23.49.csv'
output_file = r'D:\Users\jerom\Documents\Nextcloud\Documents importants\Banque\Compte courant-202407.csv'

# Define the headers for the output CSV
output_headers = [
    "Opération ventilée", "Date", "Tiers", "Montant", 
    "Catégorie 1", "Catégorie 2", "Notes", "Mode de paiement", "État", 
    "Numéro de référence", "Image", "Étiquettes"
]

# Define a function to transform the date
def transform_date(date_str):
    # Original format: dd/mm/yyyy
    # Desired format: d/m/yy
    date_obj = datetime.strptime(date_str, '%d/%m/%Y')
    return date_obj.strftime('%d/%m/%y').lstrip('0').replace('/0', '/')

# Define a function to determine the category and notes
def determine_category_two(details):
    details_lower = details.lower()
    special_cases = {
        "[Compte épargne]": ["maquoi"],
        "[Portefeuille]": ["retrait d'especes"]
    }
    categorie2 = {
        "Sandwich & pâtes": ["pasta del couz", "sam s lunch", "choboulette", "kasmi suad","panos", "mr wu"],
        "Supermarché": ["colruyt", "proxy", "lasagnes"],
        "Dons": ["amnesty", "world wide fund", "natagora", "greenpeace"],
        "Trottinette": ["bolt"],
        "Train": ["sncb"],
        "Bus": ["tec"],
        "Loyer":["loyer"],
        "Eau":["s.w.d.e."],
        "Gaz & Electricité":["eneco"],
        "Machine":["cofeo"],
        "Salaire net":["salaire"],
        "Internet":["proximus"],
        "Cambio":["cambio"],
        "Carte de crédit":["visa"],
        "Fastfood":["deliveroo", "quick", "fritapapa"],
        "Pharmarcie":["pharmacie"],
        "Équipements":["decathlon"],
        "Bar":["barnabeer"]
    }
    for category, keywords in special_cases.items():
        for keyword in keywords:
            if keyword in details_lower:
                return category, "", keyword.capitalize()
    for category, keywords in categorie2.items():
        for keyword in keywords:
            if keyword in details_lower:
                return "", category, keyword.capitalize()
    return "", "", ""

def determine_category_one(category2:str):
    print("category2 = " + category2)
    categorie1 = {
        "Alimentation":["Sandwich & pâtes", "Supermarché", "Machine", "Fastfood"],
        "Loisirs":["Dons", "Équipements", "Bar"],
        "Transport":["Trottinette", "Train", "Bus", "Cambio"],
        "Logement":["Loyer","Eau", "Gaz & Electricité", "Internet"],
        "Salaire":["Salaire net"],
        "Dépenses financières":["Carte de crédit"],
        "Santé":["Pharmacie"]
    }
    for category, keywords in categorie1.items():
        for keyword in keywords:
            #print(category2 + " = " + keyword + " ?")
            if keyword == category2:
                print(category)
                return category
    return category2

# Open the input CSV file for reading
with open(input_file, mode='r', encoding='utf-8') as infile:
    reader = csv.DictReader(infile, delimiter=';')
    
    # Open the output CSV file for writing
    with open(output_file, mode='w', newline='', encoding='utf-8') as outfile:
        writer = csv.DictWriter(outfile, fieldnames=output_headers, delimiter=';')
        writer.writeheader()

        # Process each row in the input CSV
        for row in reader:
            # Determine whether the amount is a revenue or expense
            amount = float(row["Montant"].replace(',', '.'))
            
            # Determine the category and notes
            details = row["Nom de la contrepartie"] + " " + row["Détails"]
            category1, category2, note = determine_category_two(details)

            if not category1:
                category1 = determine_category_one(category2)
            
            # Prepare the output row based on the input row
            output_row = {
                "Opération ventilée": "",
                "Date": transform_date(row["Date d'exécution"]),
                "Tiers": row["Nom de la contrepartie"],
                "Montant": f"{amount:.2f}",
                "Catégorie 1": category1,
                "Catégorie 2": category2,
                "Notes": note,
                "Mode de paiement": "Prélèvement",
                "État": "",
                "Numéro de référence": "",
                "Image": "",  # Image is not provided in the input
                "Étiquettes": ""  # Tags are not provided in the input
            }
            
            # Write the output row to the CSV file
            writer.writerow(output_row)

print(f"Data transformation complete. Output saved to {output_file}")
