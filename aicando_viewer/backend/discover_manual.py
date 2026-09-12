"""
CANDO Discovery Tool — Manual/Offline Mode (No API Key Needed)

This script works in 3 simple steps:
  1. Generates a prompt → you copy-paste it into Gemini web (gemini.google.com)
  2. You paste Gemini's response back here
  3. Script does the rest: PubChem SMILES/CID lookup + CANDO DB cross-ref + TSV export

Usage:
  python3 discover_manual.py --mesh-id MESH:D003550

Output:
  discovery_outputs/discovered_associations.tsv  (William's format)
  discovery_outputs/discovered_compounds.tsv     (William's format)
"""

import os
import sys
import json
import time
import sqlite3
import argparse
import requests
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# ── Paths ──────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_AICANDO_DIR = os.path.dirname(_BACKEND_DIR)
_CANDO_ROOT = os.path.dirname(_AICANDO_DIR)

DEFAULT_SRC_DB = os.path.join(
    _CANDO_ROOT,
    "cando-tutorial_matrix-approved-cmpds-v2.2-cmpds2inds-v2.2.db"
)
DISCOVERY_OUTPUTS_DIR = os.path.join(_BACKEND_DIR, "discovery_outputs")
DISCOVERY_REPORTS_DIR = os.path.join(_BACKEND_DIR, "discovery_reports")


# =====================================================================
# DATABASE HELPERS
# =====================================================================

def get_indication_compounds(db_path: str, mesh_id: str) -> List[Dict]:
    """Get all compounds mapped to a given indication."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT cmpd_ids FROM inds WHERE id = ?", (mesh_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return []

    cmpd_ids = eval(row[0])
    compounds = []
    for cid in cmpd_ids:
        cursor.execute("SELECT id, name FROM cmpds WHERE id = ?", (str(cid),))
        cmpd_row = cursor.fetchone()
        if cmpd_row:
            compounds.append({"id": cmpd_row[0], "name": cmpd_row[1]})
    conn.close()
    return compounds


def search_compound_by_name(db_path: str, drug_name: str, synonyms: List[str] = None) -> Optional[Dict]:
    """Search the CANDO database for a compound by name or synonyms."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    names_to_try = [drug_name.lower().replace(" ", "_").replace("-", "")]
    if synonyms:
        names_to_try.extend([s.lower().replace(" ", "_").replace("-", "") for s in synonyms[:10]])

    # Also try the raw name
    names_to_try.append(drug_name.lower())

    cursor.execute("SELECT id, name FROM cmpds")
    all_compounds = cursor.fetchall()
    conn.close()

    for cmpd_id, cmpd_name in all_compounds:
        normalized = cmpd_name.lower().replace(" ", "_").replace("-", "")
        for search_name in names_to_try:
            if search_name == normalized or search_name in normalized or normalized in search_name:
                return {"id": cmpd_id, "name": cmpd_name}

    return None


def resolve_disease_name(mesh_id: str) -> str:
    """Try to resolve a MeSH ID to a disease name from the DB."""
    db_path = DEFAULT_SRC_DB
    if not os.path.exists(db_path):
        return mesh_id
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM inds WHERE id = ?", (mesh_id,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else mesh_id
    except Exception:
        return mesh_id


# =====================================================================
# PUBCHEM HELPERS (100% Free, No API Key)
# =====================================================================

def resolve_compound_synonyms(compound_name: str) -> List[str]:
    """Query PubChem for known synonyms of a compound name."""
    clean_name = compound_name.split("(")[0].split("/")[0].strip()
    try:
        url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{requests.utils.quote(clean_name)}/synonyms/JSON"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return [compound_name]

        data = resp.json()
        synonyms = (
            data.get("InformationList", {})
                .get("Information", [{}])[0]
                .get("Synonym", [])
        )
        return synonyms[:10] if synonyms else [compound_name]
    except Exception:
        return [compound_name]


def fetch_pubchem_details(compound_name: str) -> Dict[str, str]:
    """Query PubChem for CID and Canonical/Connectivity/Isomeric SMILES string."""
    # Clean name: remove parenthetical notes like (VX-445) or (low-dose...) and take primary name before slashes
    clean_name = compound_name.split("(")[0].split("/")[0].strip()

    result = {
        "cid": f"PUBCHEM:{abs(hash(compound_name)) % 1000000}",
        "smiles": "UNKNOWN",
        "name": compound_name
    }
    try:
        url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{requests.utils.quote(clean_name)}/property/CanonicalSMILES,ConnectivitySMILES,IsomericSMILES,Title/JSON"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            info = data.get("PropertyTable", {}).get("Properties", [{}])[0]
            cid_val = info.get("CID")
            smiles_val = (
                info.get("CanonicalSMILES") or
                info.get("ConnectivitySMILES") or
                info.get("IsomericSMILES")
            )
            title_val = info.get("Title", compound_name)
            if cid_val:
                result["cid"] = f"PUBCHEM:{cid_val}"
            if smiles_val:
                result["smiles"] = smiles_val
    except Exception:
        pass
    return result


# =====================================================================
# PUBMED LITERATURE (100% Free, No API Key)
# =====================================================================

def fetch_pubmed_abstracts(search_term: str, max_results: int = 3) -> List[Dict]:
    """Fetch PubMed abstracts for a given search term."""
    try:
        encoded = search_term.replace(" ", "+")
        search_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&term={encoded}&retmode=json&retmax={max_results}"
        )
        search_resp = requests.get(search_url, timeout=10).json()
        id_list = search_resp.get('esearchresult', {}).get('idlist', [])

        if not id_list:
            return []

        abstracts = []
        for i, pmid in enumerate(id_list, start=1):
            fetch_url = (
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                f"?db=pubmed&id={pmid}&retmode=text&rettype=abstract"
            )
            abstract_text = requests.get(fetch_url, timeout=10).text.strip()
            # Truncate very long abstracts
            if len(abstract_text) > 1500:
                abstract_text = abstract_text[:1500] + "..."
            abstracts.append({
                "index": i,
                "pmid": pmid,
                "text": abstract_text
            })
            time.sleep(0.35)

        return abstracts
    except Exception as e:
        print(f"    WARNING: PubMed fetch failed: {e}")
        return []


def fetch_disease_literature(disease_name: str) -> str:
    """
    Fetch recent PubMed literature about disease treatment landscape.
    Returns a formatted evidence dossier string for the prompt.
    """
    dossier = ""

    # Search 1: Disease-modifying therapies
    print(f"    Fetching: '{disease_name} disease-modifying therapy'...", end=" ", flush=True)
    abs1 = fetch_pubmed_abstracts(f"{disease_name} disease-modifying therapy", max_results=3)
    print(f"{len(abs1)} abstract(s)")

    # Search 2: Novel treatments / drug repurposing
    print(f"    Fetching: '{disease_name} drug repurposing'...", end=" ", flush=True)
    abs2 = fetch_pubmed_abstracts(f"{disease_name} drug repurposing", max_results=2)
    print(f"{len(abs2)} abstract(s)")

    # Search 3: Recent clinical trials
    print(f"    Fetching: '{disease_name} clinical trial treatment'...", end=" ", flush=True)
    abs3 = fetch_pubmed_abstracts(f"{disease_name} clinical trial treatment", max_results=3)
    print(f"{len(abs3)} abstract(s)")

    all_abstracts = abs1 + abs2 + abs3

    if not all_abstracts:
        return "\n[No PubMed abstracts found for this disease.]\n"

    for ab in all_abstracts:
        dossier += f"\n--- Abstract {ab['index']} (PMID: {ab['pmid']}) ---\n"
        dossier += f"{ab['text']}\n"

    return dossier


# =====================================================================
# PROMPT GENERATION (RAG-Enhanced with PubMed Evidence)
# =====================================================================

def build_discovery_prompt(disease_name: str, mesh_id: str, existing_drug_names: List[str], literature: str = "") -> str:
    """Build a RAG-enhanced prompt with PubMed literature for Gemini web."""
    existing_list = "\n".join(f"  - {name}" for name in existing_drug_names)

    # Build literature section
    lit_section = ""
    if literature and literature.strip():
        lit_section = f"""
EVIDENCE DOSSIER (Recent PubMed Literature on {disease_name} Treatment):
The following are real PubMed abstracts about {disease_name} treatments and therapies.
Use this evidence to ground your suggestions in published research.
{literature}
"""

    prompt = f"""You are a Senior Biomedical Pharmacologist. Your task is to identify MISSING disease-modifying drugs for {disease_name} ({mesh_id}).

CONTEXT: The CANDO drug-repurposing platform currently maps the following drugs to {disease_name}:

{existing_list}
{lit_section}
TASK: Based on the published literature above and your biomedical knowledge, identify disease-modifying drugs that SHOULD be mapped to {disease_name} but are NOT in the list above. Focus on drugs that target the root cause or primary pathology of the disease, NOT symptomatic treatments.

RULES:
1. Only suggest drugs with published evidence of disease-modifying activity.
2. Do NOT suggest drugs already in the list above.
3. Do NOT suggest biologics, monoclonal antibodies (drugs ending in -mab), or large proteins. Only suggest small molecules.
4. For each drug, explain WHY it is disease-modifying (mechanism of action).
5. Reference specific PubMed findings from the evidence dossier when possible.
6. Grade the evidence level: clinical (FDA approved or Phase II/III), in_vivo (animal studies), in_vitro (cell/biochemical assays).
7. Include the most common generic drug name.

OUTPUT FORMAT:
Return a single JSON object. No markdown fences. Pure JSON only.

{{
  "disease": "{disease_name}",
  "mesh_id": "{mesh_id}",
  "discoveries": [
    {{
      "drug_name": "generic drug name (lowercase, underscores for spaces)",
      "drug_name_display": "Human-readable drug name",
      "mechanism": "Brief mechanism of action",
      "therapeutic_status": "disease-modifying",
      "evidence_level": "clinical | in_vivo | in_vitro",
      "reasoning": "Detailed explanation of why this drug is disease-modifying for {disease_name}. Reference specific molecular targets, pathways, or published findings.",
      "key_evidence": "Most important published evidence (include PMID if known)."
    }}
  ]
}}

IMPORTANT: Provide thorough reasoning grounded in the evidence dossier. A human expert will review your suggestions before any database changes are made. Suggest at most 15 drugs. Output raw JSON only."""

    return prompt


# =====================================================================
# RESPONSE PARSING
# =====================================================================

def parse_gemini_response(response_text: str) -> Optional[List[Dict]]:
    """Parse the Gemini web response (handles markdown fences, etc.)."""
    text = response_text.strip()

    # Strip markdown code fences if present
    if "```json" in text:
        text = text.split("```json", 1)[1]
    if "```" in text:
        text = text.split("```")[0]
    text = text.strip()

    try:
        data = json.loads(text)
        return data.get("discoveries", [])
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
                return data.get("discoveries", [])
            except json.JSONDecodeError:
                pass

    print("  ERROR: Could not parse the response as JSON.")
    print("  Make sure you copied the FULL JSON response from Gemini.")
    return None


# =====================================================================
# TSV EXPORT (William's Format)
# =====================================================================

def export_tsv_files(discoveries: List[Dict], disease_name: str, mesh_id: str) -> Tuple[str, str]:
    """Export discoveries to William's TSV format."""
    os.makedirs(DISCOVERY_OUTPUTS_DIR, exist_ok=True)

    assoc_path = os.path.join(DISCOVERY_OUTPUTS_DIR, "discovered_associations.tsv")
    cmpd_path = os.path.join(DISCOVERY_OUTPUTS_DIR, "discovered_compounds.tsv")

    # Create files with headers if they don't exist
    if not os.path.exists(assoc_path):
        with open(assoc_path, "w") as f:
            f.write("CID\tCMPD NAME\tINDICATION_MESH\tINDICATION_NAME\n")

    if not os.path.exists(cmpd_path):
        with open(cmpd_path, "w") as f:
            f.write("DrugBankID\tName\tSMILES\tApproved\n")

    exported_count = 0
    with open(assoc_path, "a") as f_assoc, open(cmpd_path, "a") as f_cmpd:
        for d in discoveries:
            name = d.get("drug_name_display", d.get("drug_name", "Unknown"))
            pubchem = d.get("pubchem_details", {})
            cid = pubchem.get("cid", f"PUBCHEM:{abs(hash(name)) % 1000000}")
            smiles = pubchem.get("smiles", "UNKNOWN")
            display_name = pubchem.get("name", name)

            f_assoc.write(f"{cid}\t{display_name}\t{mesh_id}\t{disease_name}\n")

            approved_flag = "1" if d.get("evidence_level") == "clinical" else "0"
            f_cmpd.write(f"{cid}\t{display_name}\t{smiles}\t{approved_flag}\n")
            exported_count += 1

    return assoc_path, cmpd_path


# =====================================================================
# MAIN WORKFLOW
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="CANDO Discovery Tool — Manual Mode (No API Key)"
    )
    parser.add_argument("--mesh-id", required=True, help="MeSH ID (e.g. MESH:D003550)")
    parser.add_argument("--db", default=DEFAULT_SRC_DB, help="Path to CANDO database")
    parser.add_argument("--response-file", help="Path to a text file containing Gemini's response (skip interactive paste)")
    args = parser.parse_args()

    mesh_id = args.mesh_id if args.mesh_id.startswith("MESH:") else f"MESH:{args.mesh_id}"
    disease_name = resolve_disease_name(mesh_id)

    print(f"\n{'=' * 70}")
    print(f"  CANDO Discovery Tool — Manual Mode (No API Key Needed)")
    print(f"  Disease: {disease_name} ({mesh_id})")
    print(f"{'=' * 70}\n")

    # ── STEP 1: Get current compounds ──
    print("[1/5] Loading currently mapped compounds...")
    if not os.path.exists(args.db):
        print(f"  ERROR: Database not found: {args.db}")
        sys.exit(1)

    compounds = get_indication_compounds(args.db, mesh_id)
    existing_names = [c["name"] for c in compounds]
    print(f"  Found {len(existing_names)} currently mapped compounds.\n")

    # ── STEP 2: Fetch PubMed literature (RAG evidence) ──
    print("[2/6] Fetching PubMed literature for RAG evidence (100% free)...")
    literature = fetch_disease_literature(disease_name)
    lit_size = len(literature)
    print(f"  ✓ Collected {lit_size:,} characters of PubMed evidence.\n")

    # ── STEP 3: Generate RAG-enhanced prompt ──
    print("[3/6] Generating RAG-enhanced discovery prompt...")
    prompt = build_discovery_prompt(disease_name, mesh_id, existing_names, literature=literature)

    # Save prompt to file for easy copy
    prompt_file = os.path.join(DISCOVERY_OUTPUTS_DIR, f"prompt_{mesh_id.replace(':', '_')}.txt")
    os.makedirs(DISCOVERY_OUTPUTS_DIR, exist_ok=True)
    with open(prompt_file, "w") as f:
        f.write(prompt)

    print(f"  ✓ Prompt saved to: {prompt_file}")
    print(f"  ✓ Prompt size: {len(prompt):,} characters (vs ~2,000 before)")
    print(f"\n{'─' * 70}")
    print("  ACTION REQUIRED:")
    print(f"  1. Open {prompt_file}")
    print(f"  2. Copy the ENTIRE contents")
    print(f"  3. Paste it into Gemini web (gemini.google.com)")
    print(f"  4. Copy Gemini's JSON response")
    print(f"{'─' * 70}\n")

    # ── STEP 4: Get response ──
    if args.response_file:
        print(f"[4/6] Reading Gemini response from file: {args.response_file}")
        with open(args.response_file, "r") as f:
            response_text = f.read()
    else:
        print("[4/6] Paste Gemini's response below (press Enter twice when done):")
        print("      (Or type 'quit' to exit)\n")

        lines = []
        empty_count = 0
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip().lower() == "quit":
                print("  Exiting.")
                sys.exit(0)
            if line == "":
                empty_count += 1
                if empty_count >= 2:
                    break
            else:
                empty_count = 0
            lines.append(line)
        response_text = "\n".join(lines)

    # ── STEP 4: Parse + PubChem lookup + DB cross-reference ──
    print(f"\n[5/6] Processing Gemini's suggestions...")

    suggestions = parse_gemini_response(response_text)
    if not suggestions:
        print("  ERROR: No valid suggestions found in the response.")
        sys.exit(1)

    print(f"  Found {len(suggestions)} drug suggestion(s). Looking up PubChem & CANDO DB...\n")

    results_found = []
    results_not_found = []

    for i, drug in enumerate(suggestions, start=1):
        name = drug.get("drug_name_display", drug.get("drug_name", "Unknown"))
        print(f"  [{i}/{len(suggestions)}] {name}...", end=" ", flush=True)

        # PubChem lookup (free!)
        pubchem_details = fetch_pubchem_details(name)
        drug["pubchem_details"] = pubchem_details
        smiles_status = "✓ SMILES" if pubchem_details["smiles"] != "UNKNOWN" else "✗ No SMILES"

        # PubChem synonyms for better DB matching
        synonyms = resolve_compound_synonyms(name)

        # CANDO DB cross-reference
        match = search_compound_by_name(args.db, name, synonyms)

        if match:
            drug["db_status"] = "FOUND_IN_DB"
            drug["cando_id"] = match["id"]
            drug["cando_name"] = match["name"]
            print(f"{smiles_status} | FOUND in CANDO → ID {match['id']} ({match['name']})")
            results_found.append(drug)
        else:
            drug["db_status"] = "NOT_IN_DB"
            drug["cando_id"] = None
            drug["cando_name"] = None
            print(f"{smiles_status} | NOT in CANDO (novel)")
            results_not_found.append(drug)

        time.sleep(0.4)  # PubChem rate limit

    # ── STEP 5: Export TSV files ──
    print(f"\n[6/6] Exporting to William's TSV format...")

    # Export ALL discoveries (both found and not found) to TSV
    all_discoveries = results_found + results_not_found
    assoc_file, cmpd_file = export_tsv_files(all_discoveries, disease_name, mesh_id)

    # Also save a discovery report JSON
    os.makedirs(DISCOVERY_REPORTS_DIR, exist_ok=True)
    safe_id = mesh_id.replace(":", "_")
    report_path = os.path.join(DISCOVERY_REPORTS_DIR, f"{safe_id}_discovery.json")

    report = {
        "generated_at": datetime.now().isoformat(),
        "disease": disease_name,
        "mesh_id": mesh_id,
        "mode": "manual_gemini_web",
        "status": "EXPORTED",
        "summary": {
            "total_discovered": len(all_discoveries),
            "found_in_db": len(results_found),
            "not_in_db": len(results_not_found),
        },
        "discoveries": all_discoveries,
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # ── Summary ──
    print(f"\n{'=' * 70}")
    print(f"  ✓ DISCOVERY COMPLETE for {disease_name}")
    print(f"{'=' * 70}")
    print(f"  Found in CANDO DB:    {len(results_found)} drug(s)")
    print(f"  Novel (not in DB):    {len(results_not_found)} drug(s)")
    print(f"  Total exported:       {len(all_discoveries)} drug(s)")
    print(f"\n  OUTPUT FILES:")
    print(f"    Associations TSV:   {assoc_file}")
    print(f"    Compounds TSV:      {cmpd_file}")
    print(f"    Discovery Report:   {report_path}")

    if results_found:
        print(f"\n  DRUGS ALREADY IN CANDO (can be mapped now):")
        for d in results_found:
            print(f"    ✓ {d.get('drug_name_display', '?')} → CANDO ID {d['cando_id']}")

    if results_not_found:
        print(f"\n  NOVEL DRUGS (need signature generation):")
        for d in results_not_found:
            smiles = d.get("pubchem_details", {}).get("smiles", "UNKNOWN")
            cid = d.get("pubchem_details", {}).get("cid", "?")
            print(f"    ⚙ {d.get('drug_name_display', '?')} | {cid} | SMILES: {smiles[:50]}...")

    print(f"\n  Next: Share the TSV files with William for processing!")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
