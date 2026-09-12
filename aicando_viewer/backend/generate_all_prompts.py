import os
import sys
import time
import sqlite3

# Import helpers from discover_manual
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from discover_manual import (
    get_indication_compounds,
    fetch_disease_literature,
    build_discovery_prompt,
    DEFAULT_SRC_DB,
    DISCOVERY_OUTPUTS_DIR,
)

PROMPTS_DIR = os.path.join(DISCOVERY_OUTPUTS_DIR, "prompts")

def main():
    os.makedirs(PROMPTS_DIR, exist_ok=True)
    db_path = DEFAULT_SRC_DB

    if not os.path.exists(db_path):
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    print("Fetching all diseases from CANDO database...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM inds")
    all_diseases = cursor.fetchall()
    conn.close()

    total = len(all_diseases)
    print(f"Found {total} diseases.")

    # Small delay between full disease cycles to prevent overwhelming PubMed API
    DELAY_BETWEEN_DISEASES = 0.5 

    for i, (mesh_id, disease_name) in enumerate(all_diseases, start=1):
        safe_id = mesh_id.replace(":", "_")
        prompt_file = os.path.join(PROMPTS_DIR, f"prompt_{safe_id}.txt")

        # Skip if already generated (allows for safe resuming if interrupted)
        if os.path.exists(prompt_file):
            print(f"[{i:4d}/{total}] SKIPPING (already exists): {disease_name}")
            continue

        print(f"[{i:4d}/{total}] GENERATING: {disease_name}")
        
        # 1. Get existing compounds
        compounds = get_indication_compounds(db_path, mesh_id)
        existing_names = [c["name"] for c in compounds]

        # 2. Fetch literature 
        # Note: fetch_disease_literature already does time.sleep(0.35) during requests.
        literature = fetch_disease_literature(disease_name)
        
        # 3. Build prompt
        prompt = build_discovery_prompt(disease_name, mesh_id, existing_names, literature=literature)

        # 4. Save to file
        with open(prompt_file, "w") as f:
            f.write(prompt)
            
        time.sleep(DELAY_BETWEEN_DISEASES)

    print("ALL 2,214 PROMPTS GENERATED SUCCESSFULLY!")

if __name__ == "__main__":
    main()
