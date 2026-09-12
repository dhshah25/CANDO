"""
CANDO Discovery — Batch Prompt Generator + Response Processor

STEP 1: Generate prompts for all priority diseases:
  python3 discover_batch.py generate

STEP 2: For each prompt file, paste into Gemini web and save the response
  as response_MESH_DXXXXXX.txt in the discovery_outputs/ folder.

STEP 3: Process all responses at once:
  python3 discover_batch.py process
"""

import os
import sys
import json
import time
import glob
import sqlite3
import requests
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# ── Import helpers from discover_manual ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from discover_manual import (
    get_indication_compounds,
    search_compound_by_name,
    resolve_disease_name,
    resolve_compound_synonyms,
    fetch_pubchem_details,
    fetch_disease_literature,
    parse_gemini_response,
    build_discovery_prompt,
    DEFAULT_SRC_DB,
    DISCOVERY_OUTPUTS_DIR,
    DISCOVERY_REPORTS_DIR,
)

# ── Priority diseases to discover ──
PRIORITY_DISEASES = [
    "MESH:C562707", # ACTH Deficiency, Isolated
    "MESH:D015526", # AIDS Dementia Complex
    "MESH:D000386", # AIDS-Related Complex
    "MESH:D017088", # AIDS-Related Opportunistic Infections
    "MESH:C554498", # AIDS-related Kaposi sarcoma
    "MESH:D000006", # Abdomen, Acute
    "MESH:D018784", # Abdominal Abscess
    "MESH:D000008", # Abdominal Neoplasms
    "MESH:D015746", # Abdominal Pain
    "MESH:D020434", # Abducens Nerve Diseases
    "MESH:D058739", # Aberrant Crypt Foci
    "MESH:D000014", # Abnormalities, Drug-Induced
    "MESH:D000015", # Abnormalities, Multiple
    "MESH:D009008", # Abnormalities, Severe Teratoid
    "MESH:D000026", # Abortion, Habitual
    "MESH:D000022", # Abortion, Spontaneous
    "MESH:D000033", # Abortion, Threatened
    "MESH:D000038", # Abscess
    "MESH:D015823", # Acanthamoeba Keratitis
    "MESH:D000137", # Acid-Base Imbalance
    "MESH:D000138", # Acidosis
    "MESH:D000140", # Acidosis, Lactic
    "MESH:D000141", # Acidosis, Renal Tubular
    "MESH:D000142", # Acidosis, Respiratory
    "MESH:D000151", # Acinetobacter Infections
    "MESH:D000152", # Acne Vulgaris
    "MESH:D017486", # Acneiform Eruptions
    "MESH:D020083", # Acquired Hyperostosis Syndrome
    "MESH:D000163", # Acquired Immunodeficiency Syndrome
    "MESH:C538173", # Acquired angioedema
    "MESH:D000169", # Acrodermatitis
    "MESH:C538178", # Acrodermatitis enteropathica
    "MESH:D000172", # Acromegaly
    "MESH:D000196", # Actinomycosis
    "MESH:D054058", # Acute Coronary Syndrome
    "MESH:D000208", # Acute Disease
    "MESH:D058186", # Acute Kidney Injury
    "MESH:D055371", # Acute Lung Injury
    "MESH:D059787", # Acute Pain
    "MESH:D000219", # Adams-Stokes Syndrome
    "MESH:D000224", # Addison Disease
    "MESH:D000230", # Adenocarcinoma
    "MESH:D000077192", # Adenocarcinoma of Lung
    "MESH:D002288", # Adenocarcinoma, Mucinous
    "MESH:D000236", # Adenoma
    "MESH:D011125", # Adenomatous Polyposis Coli
    "MESH:D018256", # Adenomatous Polyps
    "MESH:D000257", # Adenoviridae Infections
    "MESH:D000274", # Adiposis Dolorosa
    "MESH:D000303", # Adrenal Cortex Diseases
    "MESH:D000306", # Adrenal Cortex Neoplasms
    "MESH:D000307", # Adrenal Gland Diseases
    "MESH:D000310", # Adrenal Gland Neoplasms
    "MESH:D000312", # Adrenal Hyperplasia, Congenital
    "MESH:D000309", # Adrenal Insufficiency
    "MESH:D018268", # Adrenocortical Carcinoma
    "MESH:D000341", # Affective Disorders, Psychotic
    "MESH:D000361", # Agammaglobulinemia
    "MESH:D000370", # Ageusia
    "MESH:D010520", # Aggressive Periodontitis
    "MESH:D000377", # Agnosia
    "MESH:D000379", # Agoraphobia
    "MESH:D000380", # Agranulocytosis
    "MESH:D000402", # Airway Obstruction
    "MESH:D056151", # Airway Remodeling
    "MESH:D017109", # Akathisia, Drug-Induced
    "MESH:D000405", # Akinetic Mutism
    "MESH:D000419", # Albuminuria
    "MESH:D000430", # Alcohol Withdrawal Delirium
    "MESH:D020270", # Alcohol Withdrawal Seizures
    "MESH:D020751", # Alcohol-Induced Disorders
    "MESH:D020268", # Alcohol-Induced Disorders, Nervous System
    "MESH:D019973", # Alcohol-Related Disorders
    "MESH:D000435", # Alcoholic Intoxication
    "MESH:D000437", # Alcoholism
    "MESH:D038261", # Alexander Disease
    "MESH:D055964", # Alien Hand Syndrome
    "MESH:D000471", # Alkalosis
    "MESH:D000505", # Alopecia
    "MESH:D000506", # Alopecia Areata
    "MESH:C537055", # Alopecia universalis
    "MESH:D056769", # Alstrom Syndrome
    "MESH:D000532", # Altitude Sickness
    "MESH:D016301", # Alveolar Bone Loss
    "MESH:D000544", # Alzheimer Disease
    "MESH:D020757", # Amaurosis Fugax
    "MESH:D000550", # Amblyopia
    "MESH:D000562", # Amebiasis
    "MESH:D000568", # Amenorrhea
    "MESH:D000647", # Amnesia
    "MESH:D020324", # Amnesia, Anterograde
    "MESH:D020236", # Amnesia, Transient Global
    "MESH:D019969", # Amphetamine-Related Disorders
    "MESH:D028227", # Amyloid Neuropathies, Familial
    "MESH:D000686", # Amyloidosis
    "MESH:C538250", # Amyopathic dermatomyositis
    "MESH:D000690", # Amyotrophic Lateral Sclerosis
    "MESH:C531617", # Amyotrophic lateral sclerosis 1
    "MESH:D000707", # Anaphylaxis
    "MESH:D000740", # Anemia
]


def cmd_generate():
    """Generate prompt files for all priority diseases."""
    os.makedirs(DISCOVERY_OUTPUTS_DIR, exist_ok=True)
    db_path = DEFAULT_SRC_DB

    if not os.path.exists(db_path):
        print(f"ERROR: Database not found: {db_path}")
        sys.exit(1)

    print(f"\n{'=' * 70}")
    print(f"  CANDO Discovery — Batch Prompt Generator")
    print(f"  Generating prompts for {len(PRIORITY_DISEASES)} priority diseases")
    print(f"{'=' * 70}\n")

    for i, mesh_id in enumerate(PRIORITY_DISEASES, start=1):
        disease_name = resolve_disease_name(mesh_id)
        compounds = get_indication_compounds(db_path, mesh_id)
        existing_names = [c["name"] for c in compounds]

        print(f"  [{i:2d}/{len(PRIORITY_DISEASES)}] {disease_name} ({len(existing_names)} drugs)")

        # Fetch PubMed literature (RAG evidence — free!)
        print(f"    Fetching PubMed evidence...")
        literature = fetch_disease_literature(disease_name)

        prompt = build_discovery_prompt(disease_name, mesh_id, existing_names, literature=literature)

        safe_id = mesh_id.replace(":", "_")
        prompt_file = os.path.join(DISCOVERY_OUTPUTS_DIR, f"prompt_{safe_id}.txt")
        with open(prompt_file, "w") as f:
            f.write(prompt)

        response_file = os.path.join(DISCOVERY_OUTPUTS_DIR, f"response_{safe_id}.txt")
        status = "✓ response exists" if os.path.exists(response_file) else "⬚ needs response"

        print(f"    ✓ Prompt: {len(prompt):,} chars | {status}")
        print(f"      Save response as: response_{safe_id}.txt")
        print()

    print(f"{'─' * 70}")
    print(f"  ALL PROMPTS SAVED to: {DISCOVERY_OUTPUTS_DIR}/")
    print(f"{'─' * 70}")
    print(f"\n  NEXT STEPS:")
    print(f"  For each disease:")
    print(f"    1. Open the prompt_MESH_DXXXXXX.txt file")
    print(f"    2. Copy-paste into gemini.google.com")
    print(f"    3. Save Gemini's JSON response as response_MESH_DXXXXXX.txt")
    print(f"       in the same folder ({DISCOVERY_OUTPUTS_DIR}/)")
    print(f"\n  Then run:  python3 discover_batch.py process")
    print(f"{'─' * 70}\n")


def cmd_process():
    """Process all response files and generate TSV outputs."""
    db_path = DEFAULT_SRC_DB
    os.makedirs(DISCOVERY_OUTPUTS_DIR, exist_ok=True)
    os.makedirs(DISCOVERY_REPORTS_DIR, exist_ok=True)

    # Clear existing TSV files for fresh generation
    assoc_path = os.path.join(DISCOVERY_OUTPUTS_DIR, "discovered_associations.tsv")
    cmpd_path = os.path.join(DISCOVERY_OUTPUTS_DIR, "discovered_compounds.tsv")

    with open(assoc_path, "w") as f:
        f.write("CID\tCMPD NAME\tINDICATION_MESH\tINDICATION_NAME\tDB_STATUS\n")
    with open(cmpd_path, "w") as f:
        f.write("DrugBankID\tName\tSMILES\tApproved\tDB_STATUS\n")

    print(f"\n{'=' * 70}")
    print(f"  CANDO Discovery — Batch Response Processor")
    print(f"{'=' * 70}\n")

    total_found = 0
    total_novel = 0
    total_exported = 0
    diseases_processed = 0

    for mesh_id in PRIORITY_DISEASES:
        safe_id = mesh_id.replace(":", "_")
        response_file = os.path.join(DISCOVERY_OUTPUTS_DIR, f"response_{safe_id}.txt")

        if not os.path.exists(response_file):
            continue

        disease_name = resolve_disease_name(mesh_id)
        print(f"\n{'─' * 70}")
        print(f"  Processing: {disease_name} ({mesh_id})")
        print(f"{'─' * 70}")

        with open(response_file, "r") as f:
            response_text = f.read()

        suggestions = parse_gemini_response(response_text)
        if not suggestions:
            print(f"  ⚠ Could not parse response. Skipping.")
            continue

        print(f"  Found {len(suggestions)} suggestion(s). Looking up PubChem...")

        for j, drug in enumerate(suggestions, start=1):
            name = drug.get("drug_name_display", drug.get("drug_name", "Unknown"))
            print(f"    [{j}/{len(suggestions)}] {name}...", end=" ", flush=True)

            # PubChem lookup
            pubchem = fetch_pubchem_details(name)
            drug["pubchem_details"] = pubchem
            smiles_ok = "✓" if pubchem["smiles"] != "UNKNOWN" else "✗"

            # Synonyms + CANDO DB lookup
            synonyms = resolve_compound_synonyms(name)
            match = search_compound_by_name(db_path, name, synonyms)

            if match:
                drug["db_status"] = "FOUND_IN_DB"
                drug["cando_id"] = match["id"]
                drug["cando_name"] = match["name"]
                print(f"{smiles_ok} SMILES | FOUND → ID {match['id']}")
                total_found += 1
            else:
                drug["db_status"] = "NOT_IN_DB"
                drug["cando_id"] = None
                drug["cando_name"] = None
                print(f"{smiles_ok} SMILES | NOT in CANDO")
                total_novel += 1

            # Write to TSV files
            cid = pubchem.get("cid", f"PUBCHEM:{abs(hash(name)) % 1000000}")
            smiles = pubchem.get("smiles", "UNKNOWN")
            # Clean display name: e.g. "Elexacaftor" from "Elexacaftor (VX-445)"
            clean_display = drug.get("drug_name_display", drug.get("drug_name", name)).split("(")[0].strip()
            approved = "1" if drug.get("evidence_level") == "clinical" else "0"

            db_status = drug.get("db_status", "UNKNOWN")
            
            with open(assoc_path, "a") as fa:
                fa.write(f"{cid}\t{clean_display}\t{mesh_id}\t{disease_name}\t{db_status}\n")
            with open(cmpd_path, "a") as fc:
                fc.write(f"{cid}\t{clean_display}\t{smiles}\t{approved}\t{db_status}\n")

            total_exported += 1
            time.sleep(0.4)

        # Save individual discovery report
        report = {
            "generated_at": datetime.now().isoformat(),
            "disease": disease_name,
            "mesh_id": mesh_id,
            "mode": "manual_gemini_web",
            "status": "EXPORTED",
            "discoveries": suggestions,
        }
        report_path = os.path.join(DISCOVERY_REPORTS_DIR, f"{safe_id}_discovery.json")
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        diseases_processed += 1
        print(f"  ✓ Saved report: {os.path.basename(report_path)}")

    # ── Final Summary ──
    print(f"\n{'=' * 70}")
    print(f"  ✓ BATCH PROCESSING COMPLETE")
    print(f"{'=' * 70}")
    print(f"  Diseases processed:   {diseases_processed}")
    print(f"  Found in CANDO DB:    {total_found} drug(s)")
    print(f"  Novel (not in DB):    {total_novel} drug(s)")
    print(f"  Total exported:       {total_exported} drug(s)")
    print(f"\n  OUTPUT FILES FOR WILLIAM:")
    print(f"    {assoc_path}")
    print(f"    {cmpd_path}")
    print(f"{'=' * 70}\n")


def cmd_status():
    """Show which diseases have prompts and which have responses."""
    print(f"\n{'=' * 70}")
    print(f"  CANDO Discovery — Status")
    print(f"{'=' * 70}\n")

    done = 0
    pending = 0

    for mesh_id in PRIORITY_DISEASES:
        safe_id = mesh_id.replace(":", "_")
        disease_name = resolve_disease_name(mesh_id)
        prompt_exists = os.path.exists(os.path.join(DISCOVERY_OUTPUTS_DIR, f"prompt_{safe_id}.txt"))
        response_exists = os.path.exists(os.path.join(DISCOVERY_OUTPUTS_DIR, f"response_{safe_id}.txt"))

        if response_exists:
            status = "✓ DONE (response saved)"
            done += 1
        elif prompt_exists:
            status = "⬚ PENDING (need to paste into Gemini)"
            pending += 1
        else:
            status = "✗ NO PROMPT (run 'generate' first)"
            pending += 1

        print(f"  {status:45s}  |  {disease_name}")

    print(f"\n  Summary: {done} done, {pending} pending")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 discover_batch.py generate  — Generate prompts for all diseases")
        print("  python3 discover_batch.py status    — Check which diseases are done")
        print("  python3 discover_batch.py process   — Process all saved responses → TSV")
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == "generate":
        cmd_generate()
    elif cmd == "process":
        cmd_process()
    elif cmd == "status":
        cmd_status()
    else:
        print(f"Unknown command: {cmd}")
        print("Use: generate, status, or process")
