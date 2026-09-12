"""
CANDO Database Curation Tool — Standalone CLI (Two-Phase Workflow)

Phase 1 — CURATE (generates a report, does NOT touch the database):
  python3 curate_db.py curate --mesh-id MESH:D003550

Phase 2 — APPLY (reads the report and updates the database after human approval):
  python3 curate_db.py apply --report curation_reports/MESH_D003550.json

The production database is NEVER modified. All writes go to a separate
curated copy (cando_curated.db).
"""

import os
import sys
import json
import time
import shutil
import sqlite3
import argparse
import requests
from datetime import datetime
from typing import List, Dict, Optional, Tuple


# =====================================================================
# CONFIGURATION & DEFAULTS
# =====================================================================

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_AICANDO_DIR = os.path.dirname(_BACKEND_DIR)
_CANDO_ROOT = os.path.dirname(_AICANDO_DIR)

DEFAULT_SRC_DB = os.path.join(
    _CANDO_ROOT,
    "cando-tutorial_matrix-approved-cmpds-v2.2-cmpds2inds-v2.2.db"
)
DEFAULT_DEST_DB = os.path.join(_CANDO_ROOT, "cando_curated.db")
DEFAULT_LLM_URL = os.environ.get("LOCAL_LLM_URL", "http://localhost:8000/v1")
DEFAULT_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "gpt-oss-120b")
REPORTS_DIR = os.path.join(_BACKEND_DIR, "curation_reports")

# Gemini API defaults (used when --provider gemini is specified)
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"

# Try to load API key from the rag_app .env file
_ENV_FILE = os.path.join(_CANDO_ROOT, "rag_app", "backend", ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if _line.startswith("GEMINI_API_KEY="):
                os.environ.setdefault("GEMINI_API_KEY", _line.split("=", 1)[1])

# MeSH-to-English disease name lookup (built at import time)
TUTORIAL_DIR = os.path.join(_CANDO_ROOT, "tutorial")
IND_MAP_FILE = os.path.join(TUTORIAL_DIR, "cmpds2inds-v2.2.tsv")

_mesh_to_english: Dict[str, str] = {}
try:
    import pandas as pd
    _ind_df = pd.read_csv(IND_MAP_FILE, sep='\t')
    for _, row in _ind_df[['INDICATION_NAME', 'MESH_ID']].drop_duplicates().iterrows():
        _mesh_to_english[str(row['MESH_ID'])] = str(row['INDICATION_NAME'])
except Exception:
    pass


def resolve_disease_name(mesh_id: str) -> str:
    """Resolve a MeSH ID to its English disease name."""
    prefixed = mesh_id if mesh_id.startswith("MESH:") else f"MESH:{mesh_id}"
    return _mesh_to_english.get(prefixed, _mesh_to_english.get(mesh_id, mesh_id))


# =====================================================================
# DATABASE UTILITIES
# =====================================================================

def copy_database(src: str, dest: str, reset: bool = False) -> None:
    """Copy the source database to the destination path."""
    if reset or not os.path.exists(dest):
        print(f"  Copying database: {os.path.basename(src)} -> {os.path.basename(dest)}")
        shutil.copy2(src, dest)
        print(f"  Done. Curated database created at: {dest}")
    else:
        print(f"  Curated database already exists at: {dest}")
        print(f"  Use --reset to overwrite with a fresh copy.")


def get_indication_compounds(db_path: str, mesh_id: str) -> List[Dict]:
    """Retrieve all compounds mapped to an indication from the database."""
    prefixed = mesh_id if mesh_id.startswith("MESH:") else f"MESH:{mesh_id}"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT cmpd_ids FROM inds WHERE id = ?", (prefixed,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return []

    cmpd_ids = eval(row[0])
    results = []
    for cid in cmpd_ids:
        cursor.execute("SELECT name FROM cmpds WHERE id = ?", (str(cid),))
        name_row = cursor.fetchone()
        results.append({
            "id": str(cid),
            "name": name_row[0] if name_row else f"compound_{cid}"
        })

    conn.close()
    return results


def update_indication_compounds(
    db_path: str,
    mesh_id: str,
    approved_ids: List[str],
    excluded_ids: List[str]
) -> None:
    """
    Update the curated database by removing excluded compound IDs
    from the indication's compound list and from the compound's
    indication list.
    """
    prefixed = mesh_id if mesh_id.startswith("MESH:") else f"MESH:{mesh_id}"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Update the inds table: remove excluded compound IDs
    cursor.execute("SELECT cmpd_ids FROM inds WHERE id = ?", (prefixed,))
    row = cursor.fetchone()
    if row:
        current_ids = eval(row[0])
        # Filter out excluded IDs (keep approved + any not evaluated)
        excluded_set = set(int(eid) for eid in excluded_ids)
        new_ids = [cid for cid in current_ids if cid not in excluded_set]
        cursor.execute(
            "UPDATE inds SET cmpd_ids = ? WHERE id = ?",
            (str(new_ids), prefixed)
        )

    # Update the cmpds table: remove this indication from excluded compounds
    for exc_id in excluded_ids:
        cursor.execute("SELECT ind_ids FROM cmpds WHERE id = ?", (str(exc_id),))
        cmpd_row = cursor.fetchone()
        if cmpd_row:
            try:
                ind_list = eval(cmpd_row[0])
                if prefixed in ind_list:
                    ind_list.remove(prefixed)
                    cursor.execute(
                        "UPDATE cmpds SET ind_ids = ? WHERE id = ?",
                        (str(ind_list), str(exc_id))
                    )
            except Exception:
                pass

    conn.commit()
    conn.close()


def search_compound_by_name(
    db_path: str,
    drug_name: str,
    synonyms: Optional[List[str]] = None
) -> Optional[Dict]:
    """
    Fuzzy-search the entire CANDO cmpds table for a compound by name
    or any of its PubChem synonyms.

    Returns:
        Dict with 'id' and 'name' if found, None otherwise.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Build a list of names to search (primary name + synonyms)
    search_names = [drug_name.lower().strip()]
    if synonyms:
        search_names.extend([s.lower().strip() for s in synonyms[:20]])

    # Remove duplicates while preserving order
    seen = set()
    unique_names = []
    for n in search_names:
        if n not in seen:
            seen.add(n)
            unique_names.append(n)

    # Search by exact match first (case-insensitive)
    cursor.execute("SELECT id, name FROM cmpds")
    all_compounds = cursor.fetchall()
    conn.close()

    # Build a lookup: lowercase name -> (id, original_name)
    db_lookup = {}
    for cid, cname in all_compounds:
        db_lookup[cname.lower().strip()] = {"id": str(cid), "name": cname}

    # Try exact match against all search names
    for search_name in unique_names:
        if search_name in db_lookup:
            return db_lookup[search_name]

    # Try substring match (e.g., "ivacaftor" matches "ivacaftor_hydrochloride")
    for search_name in unique_names:
        for db_name, info in db_lookup.items():
            if search_name in db_name or db_name in search_name:
                return info

    return None


def add_compound_to_indication(
    db_path: str,
    mesh_id: str,
    cando_id: str
) -> bool:
    """
    Add a compound-indication mapping to the curated database.

    Updates both sides of the relationship:
      - Adds cando_id to the indication's cmpd_ids list
      - Adds mesh_id to the compound's ind_ids list

    Returns True if the mapping was added, False if it already existed.
    """
    prefixed = mesh_id if mesh_id.startswith("MESH:") else f"MESH:{mesh_id}"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Update inds table: add compound ID to cmpd_ids
    cursor.execute("SELECT cmpd_ids FROM inds WHERE id = ?", (prefixed,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        print(f"    WARNING: Indication {prefixed} not found in database.")
        return False

    current_ids = eval(row[0])
    cid_int = int(cando_id)

    if cid_int in current_ids:
        conn.close()
        return False  # Already mapped

    current_ids.append(cid_int)
    cursor.execute(
        "UPDATE inds SET cmpd_ids = ? WHERE id = ?",
        (str(current_ids), prefixed)
    )

    # Update cmpds table: add indication to ind_ids
    cursor.execute("SELECT ind_ids FROM cmpds WHERE id = ?", (str(cando_id),))
    cmpd_row = cursor.fetchone()
    if cmpd_row:
        try:
            ind_list = eval(cmpd_row[0])
            if prefixed not in ind_list:
                ind_list.append(prefixed)
                cursor.execute(
                    "UPDATE cmpds SET ind_ids = ? WHERE id = ?",
                    (str(ind_list), str(cando_id))
                )
        except Exception:
            pass

    conn.commit()
    conn.close()
    return True


# =====================================================================
# PUBMED LITERATURE RETRIEVAL
# =====================================================================

def fetch_pubmed_abstracts(
    drug_name: str,
    disease_name: str,
    max_results: int = 3
) -> List[Dict]:
    """Retrieve abstracts from NCBI PubMed E-utilities API."""
    try:
        search_term = f"{drug_name}+{disease_name}".replace(" ", "+")
        search_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&term={search_term}&retmode=json&retmax={max_results}"
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
            abstracts.append({
                "index": i,
                "pmid": pmid,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "text": abstract_text
            })
            time.sleep(0.35)  # NCBI rate-limit compliance

        return abstracts

    except Exception as e:
        print(f"    WARNING: PubMed request failed for {drug_name}: {e}")
        return []


# =====================================================================
# PUBCHEM COMPOUND NAME RESOLUTION
# =====================================================================

def resolve_compound_synonyms(compound_name: str) -> List[str]:
    """Query PubChem for known synonyms of a compound name."""
    try:
        url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{requests.utils.quote(compound_name)}/synonyms/JSON"
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
        # Return up to 10 synonyms (including the original name)
        return synonyms[:10] if synonyms else [compound_name]

    except Exception:
        return [compound_name]


# =====================================================================
# LLM INTEGRATION (Local LLM or Gemini API)
# =====================================================================

def call_llm(
    prompt: str,
    llm_url: str,
    llm_model: str,
    api_key: Optional[str] = None,
    temperature: float = 0.1,
    max_retries: int = 3
) -> Optional[str]:
    """
    Call an LLM via OpenAI-compatible /chat/completions API.

    Works with:
      - Local LLM servers (vLLM, Ollama, etc.) — no api_key needed
      - Gemini API (via its OpenAI-compatible endpoint) — pass api_key

    Automatically retries on 429 (rate limit) errors with exponential backoff.

    Args:
        prompt: The full prompt text to send.
        llm_url: Base URL (e.g. http://localhost:8000/v1 or Gemini URL).
        llm_model: Model name to use.
        api_key: Optional API key (sent as Bearer token).
        temperature: Sampling temperature (low for deterministic output).
        max_retries: Max number of retries on 429 rate-limit errors.

    Returns:
        The LLM's response text, or None on failure.
    """
    endpoint = f"{llm_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": llm_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a biomedical pharmacology expert. You evaluate "
                    "drug-disease associations based strictly on provided "
                    "PubMed evidence. Output valid JSON only."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": temperature,
        "max_tokens": 4096
    }

    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=120)

            if resp.status_code == 429:
                # Rate limit hit — wait and retry
                wait_time = 30 * (2 ** attempt)  # 30s, 60s, 120s
                print(f"    Rate limit hit (429). Waiting {wait_time}s before retry "
                      f"({attempt + 1}/{max_retries})...")
                time.sleep(wait_time)
                continue

            if resp.status_code != 200:
                print(f"    WARNING: LLM returned status {resp.status_code}: {resp.text[:300]}")
                return None

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content

        except requests.exceptions.ConnectionError:
            print(f"    ERROR: Cannot connect to LLM server at {endpoint}")
            print(f"           Make sure the LLM server is running (or check your API key).")
            return None
        except Exception as e:
            print(f"    ERROR: LLM call failed: {e}")
            return None

    print(f"    ERROR: Exhausted all {max_retries} retries due to rate limiting.")
    return None


# =====================================================================
# CURATION PROMPT & ADJUDICATION
# =====================================================================

def build_curation_prompt(
    disease_name: str,
    mesh_id: str,
    compounds_with_evidence: List[Dict]
) -> str:
    """
    Build the adjudication prompt for the local LLM.

    The prompt instructs the LLM to classify each compound as
    disease-modifying or symptomatic/palliative and grade the
    evidence level.
    """
    # Build the evidence dossier section
    dossier = ""
    for entry in compounds_with_evidence:
        cmpd_name = entry["name"]
        synonyms = entry.get("synonyms", [])
        abstracts = entry.get("abstracts", [])

        dossier += f"\n{'=' * 50}\n"
        dossier += f"COMPOUND: {cmpd_name} (CANDO ID: {entry['id']})\n"
        if synonyms and len(synonyms) > 1:
            dossier += f"KNOWN SYNONYMS: {', '.join(synonyms[:5])}\n"
        dossier += f"{'=' * 50}\n"

        if not abstracts:
            dossier += "  [No PubMed abstracts found for this drug-disease pair.]\n"
            continue

        for ab in abstracts:
            dossier += f"\n  Abstract {ab['index']} (PMID: {ab['pmid']}):\n"
            dossier += f"  {ab['text']}\n"

    prompt = f"""You are a Senior Biomedical Pharmacologist evaluating drug-indication associations for the CANDO drug-repurposing platform.

TASK: For each compound listed below, determine whether it is a TRUE DISEASE-MODIFYING therapeutic for {disease_name}, or merely a SYMPTOMATIC/PALLIATIVE treatment for secondary symptoms.

DISEASE CONTEXT:
  Disease: {disease_name}
  MeSH ID: {mesh_id}

EVIDENCE DOSSIER (PubMed Abstracts):
{dossier}

ADJUDICATION RULES:
1. A compound is "disease-modifying" if it targets the underlying pathology of {disease_name} (e.g., for Cystic Fibrosis, CFTR modulators like ivacaftor are disease-modifying).
2. A compound is "symptomatic/palliative" if it only treats secondary symptoms (e.g., antibiotics for lung infections in CF patients are NOT disease-modifying for CF itself).
3. If no PubMed abstracts were found, classify as "insufficient_evidence".

EVIDENCE GRADING (assign the highest level supported by the abstracts):
  - "clinical": Phase II/III clinical trial data or FDA approval for this specific indication.
  - "in_vivo": Animal model studies showing therapeutic effect.
  - "in_vitro": Cell-line or biochemical assay data showing mechanistic activity.
  - "none": No direct evidence found in the provided abstracts.

OUTPUT FORMAT:
Return a single JSON object with this exact structure. No markdown fences. Pure JSON only.

{{
  "disease": "{disease_name}",
  "mesh_id": "{mesh_id}",
  "evaluations": [
    {{
      "cando_id": "compound CANDO ID",
      "compound_name": "compound name",
      "therapeutic_status": "disease-modifying | symptomatic/palliative | insufficient_evidence",
      "evidence_level": "clinical | in_vivo | in_vitro | none",
      "reasoning": "A detailed explanation of WHY this compound is or is not disease-modifying for {disease_name}. Reference specific mechanisms, targets, or pathways. Explain what the drug actually does and why that does or does not address the root cause of the disease.",
      "key_evidence": "Direct quote or specific finding from the abstract that most strongly supports the classification. Include the PMID.",
      "verdict": "APPROVE | EXCLUDE | NEEDS_REVIEW"
    }}
  ]
}}

VERDICT RULES:
  - APPROVE: disease-modifying with evidence level clinical, in_vivo, or in_vitro.
  - EXCLUDE: symptomatic/palliative regardless of evidence level.
  - NEEDS_REVIEW: insufficient_evidence or ambiguous cases.

IMPORTANT: Provide thorough, detailed reasoning for each compound. The reasoning
will be reviewed by a human expert before any database changes are made.

Evaluate ALL compounds. Output raw JSON only."""

    return prompt


def parse_llm_response(response_text: str) -> Optional[Dict]:
    """Parse the LLM's JSON response, handling common formatting issues."""
    if not response_text:
        return None

    # Strip markdown code fences if present
    clean = response_text.strip()
    if clean.startswith("```"):
        # Remove opening fence (with optional language tag)
        first_newline = clean.index('\n') if '\n' in clean else 3
        clean = clean[first_newline + 1:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"    WARNING: Failed to parse LLM JSON: {e}")
        print(f"    Raw response (first 500 chars): {response_text[:500]}")
        return None


# =====================================================================
# DISCOVERY PROMPT & RESPONSE
# =====================================================================

def build_discovery_prompt(
    disease_name: str,
    mesh_id: str,
    existing_drug_names: List[str]
) -> str:
    """
    Build a prompt that asks the LLM to suggest disease-modifying drugs
    that are currently MISSING from the database mapping.
    """
    existing_list = "\n".join(f"  - {name}" for name in existing_drug_names)

    prompt = f"""You are a Senior Biomedical Pharmacologist. Your task is to identify MISSING disease-modifying drugs for {disease_name} ({mesh_id}).

CONTEXT: The CANDO drug-repurposing platform currently maps the following drugs to {disease_name}:

{existing_list}

TASK: Identify disease-modifying drugs that SHOULD be mapped to {disease_name} but are NOT in the list above. Focus on drugs that target the root cause or primary pathology of the disease, NOT symptomatic treatments.

RULES:
1. Only suggest drugs with published evidence of disease-modifying activity.
2. Do NOT suggest drugs already in the list above.
3. For each drug, explain WHY it is disease-modifying (mechanism of action).
4. Grade the evidence level: clinical (FDA approved or Phase II/III), in_vivo (animal studies), in_vitro (cell/biochemical assays).
5. Include the most common generic drug name.

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
      "reasoning": "Detailed explanation of why this drug is disease-modifying for {disease_name}. Reference specific molecular targets, pathways, or trial results.",
      "key_evidence": "Most important published evidence (include PMID if known)."
    }}
  ]
}}

IMPORTANT: Provide thorough reasoning. A human expert will review your suggestions before any database changes are made. Suggest at most 15 drugs. Output raw JSON only."""

    return prompt


def parse_discovery_response(response_text: str) -> Optional[List[Dict]]:
    """Parse the LLM's discovery JSON response into a list of suggestions."""
    parsed = parse_llm_response(response_text)
    if parsed is None:
        return None
    return parsed.get("discoveries", [])


# =====================================================================
# CURATION REPORT — GENERATION & DISPLAY
# =====================================================================

def save_curation_report(
    mesh_id: str,
    disease_name: str,
    evaluations: List[Dict],
    dest_db: str
) -> str:
    """
    Save the LLM's adjudication results to a JSON report file.
    The report is meant to be reviewed by a human before applying changes.

    Returns the path to the saved report file.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)

    # Build filename from mesh ID (e.g. MESH_D003550.json)
    safe_id = mesh_id.replace(":", "_")
    report_path = os.path.join(REPORTS_DIR, f"{safe_id}.json")

    report = {
        "generated_at": datetime.now().isoformat(),
        "disease": disease_name,
        "mesh_id": mesh_id,
        "target_database": dest_db,
        "status": "PENDING_REVIEW",
        "summary": {
            "total_compounds": len(evaluations),
            "approve": len([e for e in evaluations if e.get("verdict") == "APPROVE"]),
            "exclude": len([e for e in evaluations if e.get("verdict") == "EXCLUDE"]),
            "needs_review": len([e for e in evaluations if e.get("verdict") == "NEEDS_REVIEW"]),
        },
        "evaluations": evaluations,
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    return report_path


def print_detailed_report(evaluations: List[Dict], disease_name: str) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Print a detailed, human-readable report of the LLM's reasoning
    for each compound. Organized into three sections: Removals, Approvals,
    and Needs Review.
    """
    approved = [e for e in evaluations if e.get("verdict") == "APPROVE"]
    excluded = [e for e in evaluations if e.get("verdict") == "EXCLUDE"]
    review = [e for e in evaluations if e.get("verdict") == "NEEDS_REVIEW"]

    print(f"\n{'#' * 80}")
    print(f"  CURATION REPORT: {disease_name}")
    print(f"  {len(approved)} approved  |  {len(excluded)} to remove  |  {len(review)} need review")
    print(f"{'#' * 80}")

    # ── Section 1: Compounds recommended for REMOVAL ──
    if excluded:
        print(f"\n{'=' * 80}")
        print(f"  RECOMMENDED FOR REMOVAL (False/Misleading Mappings)")
        print(f"  These drugs are NOT disease-modifying for {disease_name}.")
        print(f"  The LLM recommends removing them from the database.")
        print(f"{'=' * 80}")

        for i, ev in enumerate(excluded, start=1):
            name = ev.get("compound_name", "Unknown")
            cid = ev.get("cando_id", "?")
            status = ev.get("therapeutic_status", "unknown")
            evidence = ev.get("evidence_level", "none")
            reasoning = ev.get("reasoning", "No reasoning provided.")
            key_ev = ev.get("key_evidence", "No key evidence cited.")

            print(f"\n  [{i}] {name} (CANDO ID: {cid})")
            print(f"      Status:   {status}")
            print(f"      Evidence: {evidence}")
            print(f"      ┌─ WHY REMOVE: ─────────────────────────────────────────")
            # Word-wrap reasoning at ~70 chars
            for line in _wrap_text(reasoning, 70):
                print(f"      │  {line}")
            print(f"      ├─ KEY EVIDENCE: ───────────────────────────────────────")
            for line in _wrap_text(key_ev, 70):
                print(f"      │  {line}")
            print(f"      └──────────────────────────────────────────────────────")

    # ── Section 2: Compounds APPROVED as disease-modifying ──
    if approved:
        print(f"\n{'=' * 80}")
        print(f"  APPROVED (True Disease-Modifying Drugs)")
        print(f"  These drugs target the root cause of {disease_name}.")
        print(f"  The LLM recommends keeping them in the database.")
        print(f"{'=' * 80}")

        for i, ev in enumerate(approved, start=1):
            name = ev.get("compound_name", "Unknown")
            cid = ev.get("cando_id", "?")
            status = ev.get("therapeutic_status", "unknown")
            evidence = ev.get("evidence_level", "none")
            reasoning = ev.get("reasoning", "No reasoning provided.")
            key_ev = ev.get("key_evidence", "No key evidence cited.")

            print(f"\n  [{i}] {name} (CANDO ID: {cid})")
            print(f"      Status:   {status}")
            print(f"      Evidence: {evidence}")
            print(f"      ┌─ WHY KEEP: ──────────────────────────────────────────")
            for line in _wrap_text(reasoning, 70):
                print(f"      │  {line}")
            print(f"      ├─ KEY EVIDENCE: ───────────────────────────────────────")
            for line in _wrap_text(key_ev, 70):
                print(f"      │  {line}")
            print(f"      └──────────────────────────────────────────────────────")

    # ── Section 3: Compounds needing further review ──
    if review:
        print(f"\n{'=' * 80}")
        print(f"  NEEDS REVIEW (Insufficient or Ambiguous Evidence)")
        print(f"  The LLM could not make a confident decision on these drugs.")
        print(f"{'=' * 80}")

        for i, ev in enumerate(review, start=1):
            name = ev.get("compound_name", "Unknown")
            cid = ev.get("cando_id", "?")
            reasoning = ev.get("reasoning", "No reasoning provided.")

            print(f"\n  [{i}] {name} (CANDO ID: {cid})")
            print(f"      ┌─ NOTES: ─────────────────────────────────────────────")
            for line in _wrap_text(reasoning, 70):
                print(f"      │  {line}")
            print(f"      └──────────────────────────────────────────────────────")

    print(f"\n{'#' * 80}")
    print(f"  END OF REPORT")
    print(f"{'#' * 80}\n")

    return approved, excluded, review


def _wrap_text(text: str, width: int) -> List[str]:
    """Simple word-wrap for terminal display."""
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        if len(current_line) + len(word) + 1 > width:
            lines.append(current_line)
            current_line = word
        else:
            current_line = f"{current_line} {word}" if current_line else word
    if current_line:
        lines.append(current_line)
    return lines if lines else [""]


# =====================================================================
# PHASE 1: CURATE (Generate Report — No DB Changes)
# =====================================================================

def run_curate(
    mesh_id: str,
    src_db: str,
    dest_db: str,
    llm_url: str,
    llm_model: str,
    api_key: Optional[str] = None,
    reset: bool = False
) -> None:
    """
    Phase 1: Gather evidence, call the LLM, and generate a curation report.

    This phase does NOT modify any database. It produces a JSON report
    in curation_reports/ that a human must review before applying.
    """
    prefixed = mesh_id if mesh_id.startswith("MESH:") else f"MESH:{mesh_id}"
    disease_name = resolve_disease_name(prefixed)

    provider_label = "Gemini API" if api_key else "Local LLM"
    print(f"\n{'#' * 60}")
    print(f"  CANDO Database Curation Tool — Phase 1: CURATE")
    print(f"  Target: {disease_name} ({prefixed})")
    print(f"  Provider: {provider_label}")
    print(f"  Model: {llm_model} @ {llm_url}")
    print(f"  NOTE: This phase generates a report only. No DB changes.")
    print(f"{'#' * 60}\n")

    # Step 1: Ensure curated DB copy exists
    print("[1/5] Initializing curated database copy...")
    if not os.path.exists(src_db):
        print(f"  ERROR: Source database not found at: {src_db}")
        sys.exit(1)
    copy_database(src_db, dest_db, reset=reset)

    # Step 2: Retrieve compounds for this indication
    print(f"\n[2/5] Retrieving compounds for {disease_name}...")
    compounds = get_indication_compounds(dest_db, prefixed)
    if not compounds:
        print(f"  No compounds found for {prefixed} in the database.")
        return
    print(f"  Found {len(compounds)} mapped compounds.")

    # Step 3: Fetch PubMed literature + PubChem synonyms
    # Check for cached evidence dossier first (avoids re-fetching if LLM failed)
    safe_id = prefixed.replace(":", "_")
    cache_path = os.path.join(REPORTS_DIR, f"{safe_id}_evidence.json")
    os.makedirs(REPORTS_DIR, exist_ok=True)

    if os.path.exists(cache_path):
        print(f"\n[3/5] Loading cached evidence dossier from previous run...")
        with open(cache_path, "r") as f:
            compounds_with_evidence = json.load(f)
        print(f"  Loaded {len(compounds_with_evidence)} compounds with evidence (cached).")
    else:
        print(f"\n[3/5] Fetching PubMed literature and PubChem synonyms...")
        compounds_with_evidence = []
        for i, cmpd in enumerate(compounds, start=1):
            name = cmpd["name"]
            print(f"  [{i}/{len(compounds)}] {name}...", end=" ", flush=True)

            # Fetch synonyms from PubChem
            synonyms = resolve_compound_synonyms(name)

            # Fetch PubMed abstracts
            abstracts = fetch_pubmed_abstracts(name, disease_name)
            print(f"{len(abstracts)} abstracts, {len(synonyms)} synonyms")

            compounds_with_evidence.append({
                "id": cmpd["id"],
                "name": name,
                "synonyms": synonyms,
                "abstracts": abstracts
            })

        # Cache the evidence dossier to disk
        with open(cache_path, "w") as f:
            json.dump(compounds_with_evidence, f, indent=2)
        print(f"  Evidence dossier cached to: {os.path.basename(cache_path)}")

    # Step 4: Build prompt and call the LLM
    provider_label = "Gemini API" if api_key else "local LLM"
    print(f"\n[4/5] Sending evidence dossier to {provider_label} for adjudication...")
    prompt = build_curation_prompt(disease_name, prefixed, compounds_with_evidence)

    llm_response = call_llm(prompt, llm_url, llm_model, api_key=api_key)
    if llm_response is None:
        print("  ERROR: No response from LLM. Aborting.")
        print(f"  Evidence dossier is saved at: {cache_path}")
        print(f"  Re-run the same command to retry (PubMed data will be loaded from cache).")
        return

    parsed = parse_llm_response(llm_response)
    if parsed is None:
        print("  ERROR: Could not parse LLM response. Aborting.")
        return

    evaluations = parsed.get("evaluations", [])
    if not evaluations:
        print("  WARNING: LLM returned no evaluations.")
        return

    # Step 5: Display detailed reasoning and save report
    print(f"\n[5/5] Adjudication complete. Generating report...")
    approved, excluded, review = print_detailed_report(evaluations, disease_name)

    report_path = save_curation_report(
        mesh_id=prefixed,
        disease_name=disease_name,
        evaluations=evaluations,
        dest_db=dest_db
    )

    print(f"  Report saved to: {report_path}")
    print(f"\n  *** NO DATABASE CHANGES HAVE BEEN MADE ***")
    print(f"\n  To apply these changes after reviewing the report, run:")
    print(f"    python3 curate_db.py apply --report {report_path}")


# =====================================================================
# PHASE 2: APPLY (Read Report & Update DB After Human Approval)
# =====================================================================

def run_apply(report_path: str, dest_db: str = None) -> None:
    """
    Phase 2: Read a curation report and apply the changes to the database
    after getting explicit human approval.

    The user reviews the report JSON (or the terminal output from Phase 1)
    and then runs this command to permanently update the curated database.
    """
    if not os.path.exists(report_path):
        print(f"  ERROR: Report file not found: {report_path}")
        sys.exit(1)

    with open(report_path, "r") as f:
        report = json.load(f)

    disease_name = report.get("disease", "Unknown")
    mesh_id = report.get("mesh_id", "Unknown")
    status = report.get("status", "UNKNOWN")
    target_db = dest_db or report.get("target_database", DEFAULT_DEST_DB)
    evaluations = report.get("evaluations", [])

    if status == "APPLIED":
        print(f"  This report has already been applied to the database.")
        print(f"  Applied at: {report.get('applied_at', 'unknown')}")
        return

    print(f"\n{'#' * 60}")
    print(f"  CANDO Database Curation Tool — Phase 2: APPLY")
    print(f"  Disease: {disease_name} ({mesh_id})")
    print(f"  Report: {os.path.basename(report_path)}")
    print(f"  Target DB: {os.path.basename(target_db)}")
    print(f"{'#' * 60}\n")

    # Show the detailed reasoning report again
    approved, excluded, review = print_detailed_report(evaluations, disease_name)

    excluded_ids = [ev.get("cando_id", "") for ev in excluded]
    approved_ids = [ev.get("cando_id", "") for ev in approved]

    if not excluded_ids:
        print("  No compounds marked for exclusion. Database unchanged.")
        return

    # Ask for explicit human approval
    print(f"  ACTION: The following {len(excluded_ids)} compound(s) will be")
    print(f"  PERMANENTLY REMOVED from {disease_name} in '{os.path.basename(target_db)}':")
    print()
    for ev in excluded:
        print(f"    ✗  {ev.get('compound_name', 'Unknown')} (ID: {ev.get('cando_id', '?')})")
    print()
    print(f"  The following {len(approved_ids)} compound(s) will be KEPT:")
    print()
    for ev in approved:
        print(f"    ✓  {ev.get('compound_name', 'Unknown')} (ID: {ev.get('cando_id', '?')})")

    if review:
        print(f"\n  The following {len(review)} compound(s) will NOT be touched (need manual review):")
        for ev in review:
            print(f"    ?  {ev.get('compound_name', 'Unknown')} (ID: {ev.get('cando_id', '?')})")

    print()
    try:
        confirm = input("  Do you approve these changes? Type 'yes' to confirm: ")
    except (EOFError, KeyboardInterrupt):
        confirm = ""

    if confirm.strip().lower() != "yes":
        print("  Aborted. No changes made to the database.")
        return

    # Apply the changes
    if not os.path.exists(target_db):
        print(f"  ERROR: Target database not found: {target_db}")
        return

    update_indication_compounds(target_db, mesh_id, approved_ids, excluded_ids)

    # Mark the report as applied
    report["status"] = "APPLIED"
    report["applied_at"] = datetime.now().isoformat()
    report["applied_by"] = "manual_approval"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n  ✓ Database updated successfully.")
    print(f"    Removed {len(excluded_ids)} false mapping(s) from {disease_name}.")
    print(f"    Curated database: {target_db}")
    print(f"    Report marked as APPLIED: {report_path}")


# =====================================================================
# DISCOVERY MODE — PHASE 1: DISCOVER (Find Missing Drugs)
# =====================================================================

DISCOVERY_REPORTS_DIR = os.path.join(_BACKEND_DIR, "discovery_reports")


def save_discovery_report(
    mesh_id: str,
    disease_name: str,
    discoveries: List[Dict],
    dest_db: str
) -> str:
    """Save the discovery results to a JSON report file."""
    os.makedirs(DISCOVERY_REPORTS_DIR, exist_ok=True)
    safe_id = mesh_id.replace(":", "_")
    report_path = os.path.join(DISCOVERY_REPORTS_DIR, f"{safe_id}_discovery.json")

    found_in_db = [d for d in discoveries if d.get("db_status") == "FOUND_IN_DB"]
    not_in_db = [d for d in discoveries if d.get("db_status") == "NOT_IN_DB"]

    report = {
        "generated_at": datetime.now().isoformat(),
        "disease": disease_name,
        "mesh_id": mesh_id,
        "target_database": dest_db,
        "status": "PENDING_REVIEW",
        "summary": {
            "total_discovered": len(discoveries),
            "found_in_db": len(found_in_db),
            "not_in_db": len(not_in_db),
        },
        "discoveries": discoveries,
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    return report_path


def print_discovery_report(
    discoveries: List[Dict],
    disease_name: str
) -> Tuple[List[Dict], List[Dict]]:
    """
    Print a detailed, human-readable discovery report.
    Returns (found_in_db, not_in_db) lists.
    """
    found_in_db = [d for d in discoveries if d.get("db_status") == "FOUND_IN_DB"]
    not_in_db = [d for d in discoveries if d.get("db_status") == "NOT_IN_DB"]

    print(f"\n{'#' * 80}")
    print(f"  DISCOVERY REPORT: {disease_name}")
    print(f"  {len(found_in_db)} ready to map  |  {len(not_in_db)} need simulation")
    print(f"{'#' * 80}")

    # ── Section 1: Drugs found in CANDO DB (ready to map) ──
    if found_in_db:
        print(f"\n{'=' * 80}")
        print(f"  DISCOVERED — Already in CANDO Database (Ready to Map)")
        print(f"  These drugs are disease-modifying but NOT currently linked to {disease_name}.")
        print(f"{'=' * 80}")

        for i, d in enumerate(found_in_db, start=1):
            name = d.get("drug_name_display", d.get("drug_name", "Unknown"))
            cid = d.get("cando_id", "?")
            cando_name = d.get("cando_name", "?")
            evidence = d.get("evidence_level", "none")
            mechanism = d.get("mechanism", "")
            reasoning = d.get("reasoning", "No reasoning provided.")
            key_ev = d.get("key_evidence", "No key evidence cited.")

            print(f"\n  [{i}] {name} → matched CANDO compound: {cando_name} (ID: {cid})")
            print(f"      Evidence: {evidence}")
            print(f"      Mechanism: {mechanism}")
            print(f"      ┌─ WHY ADD: ──────────────────────────────────────────")
            for line in _wrap_text(reasoning, 70):
                print(f"      │  {line}")
            print(f"      ├─ KEY EVIDENCE: ───────────────────────────────────────")
            for line in _wrap_text(key_ev, 70):
                print(f"      │  {line}")
            print(f"      └──────────────────────────────────────────────────────")

    # ── Section 2: Drugs NOT in CANDO DB (need simulation) ──
    if not_in_db:
        print(f"\n{'=' * 80}")
        print(f"  DISCOVERED — NOT in CANDO Database (Need Simulation First)")
        print(f"  These drugs need molecular docking before they can be added.")
        print(f"{'=' * 80}")

        for i, d in enumerate(not_in_db, start=1):
            name = d.get("drug_name_display", d.get("drug_name", "Unknown"))
            evidence = d.get("evidence_level", "none")
            mechanism = d.get("mechanism", "")
            reasoning = d.get("reasoning", "No reasoning provided.")
            key_ev = d.get("key_evidence", "No key evidence cited.")

            print(f"\n  [{i}] {name}")
            print(f"      Evidence: {evidence}")
            print(f"      Mechanism: {mechanism}")
            print(f"      ┌─ WHY ADD: ──────────────────────────────────────────")
            for line in _wrap_text(reasoning, 70):
                print(f"      │  {line}")
            print(f"      ├─ KEY EVIDENCE: ───────────────────────────────────────")
            for line in _wrap_text(key_ev, 70):
                print(f"      │  {line}")
            print(f"      ├─ ACTION NEEDED: ─────────────────────────────────────")
            print(f"      │  Run CANDO docking simulation to compute protein")
            print(f"      │  interaction signature before this drug can be mapped.")
            print(f"      └──────────────────────────────────────────────────────")

    if not found_in_db and not not_in_db:
        print(f"\n  No new drugs discovered for {disease_name}.")
        print(f"  The current mapping appears comprehensive.")

    print(f"\n{'#' * 80}")
    print(f"  END OF DISCOVERY REPORT")
    print(f"{'#' * 80}\n")

    return found_in_db, not_in_db


def run_discover(
    mesh_id: str,
    src_db: str,
    dest_db: str,
    llm_url: str,
    llm_model: str,
    api_key: Optional[str] = None,
    reset: bool = False
) -> None:
    """
    Discovery Mode Phase 1: Ask the LLM to suggest missing disease-modifying
    drugs, then cross-reference each suggestion against the CANDO database.

    Generates a discovery report JSON. Does NOT modify the database.
    """
    prefixed = mesh_id if mesh_id.startswith("MESH:") else f"MESH:{mesh_id}"
    disease_name = resolve_disease_name(prefixed)

    provider_label = "Gemini API" if api_key else "Local LLM"
    print(f"\n{'#' * 60}")
    print(f"  CANDO Discovery Tool — Phase 1: DISCOVER")
    print(f"  Target: {disease_name} ({prefixed})")
    print(f"  Provider: {provider_label}")
    print(f"  Model: {llm_model} @ {llm_url}")
    print(f"  NOTE: This phase generates a report only. No DB changes.")
    print(f"{'#' * 60}\n")

    # Step 1: Ensure curated DB copy exists
    print("[1/4] Initializing curated database copy...")
    if not os.path.exists(src_db):
        print(f"  ERROR: Source database not found at: {src_db}")
        sys.exit(1)
    copy_database(src_db, dest_db, reset=reset)

    # Step 2: Get current drug list
    print(f"\n[2/4] Retrieving currently mapped compounds for {disease_name}...")
    compounds = get_indication_compounds(dest_db, prefixed)
    existing_names = [c["name"] for c in compounds]
    print(f"  Found {len(existing_names)} currently mapped compounds.")

    # Step 3: Ask LLM to suggest missing drugs
    provider_label = "Gemini API" if api_key else "local LLM"
    print(f"\n[3/4] Asking {provider_label} to suggest missing disease-modifying drugs...")
    prompt = build_discovery_prompt(disease_name, prefixed, existing_names)

    llm_response = call_llm(prompt, llm_url, llm_model, api_key=api_key)
    if llm_response is None:
        print("  ERROR: No response from LLM. Aborting.")
        return

    suggestions = parse_discovery_response(llm_response)
    if not suggestions:
        print("  No new drugs suggested by the LLM.")
        return

    print(f"  LLM suggested {len(suggestions)} potential drug(s).")

    # Step 4: Cross-reference each suggestion against the CANDO database
    print(f"\n[4/4] Cross-referencing suggestions against CANDO database...")
    enriched_discoveries = []

    for i, suggestion in enumerate(suggestions, start=1):
        drug_name = suggestion.get("drug_name", "")
        display_name = suggestion.get("drug_name_display", drug_name)
        print(f"  [{i}/{len(suggestions)}] {display_name}...", end=" ", flush=True)

        # Resolve PubChem synonyms for fuzzy matching
        synonyms = resolve_compound_synonyms(drug_name)

        # Search the CANDO database
        match = search_compound_by_name(dest_db, drug_name, synonyms)

        if match:
            suggestion["db_status"] = "FOUND_IN_DB"
            suggestion["cando_id"] = match["id"]
            suggestion["cando_name"] = match["name"]
            print(f"FOUND → CANDO ID {match['id']} ({match['name']})")
        else:
            suggestion["db_status"] = "NOT_IN_DB"
            suggestion["cando_id"] = None
            suggestion["cando_name"] = None
            print(f"NOT FOUND in CANDO database")

        suggestion["synonyms_checked"] = synonyms[:5]
        enriched_discoveries.append(suggestion)
        time.sleep(0.35)  # PubChem rate limit

    # Display the discovery report
    found_in_db, not_in_db = print_discovery_report(enriched_discoveries, disease_name)

    # Save the discovery report
    report_path = save_discovery_report(
        mesh_id=prefixed,
        disease_name=disease_name,
        discoveries=enriched_discoveries,
        dest_db=dest_db
    )

    print(f"  Report saved to: {report_path}")
    print(f"\n  *** NO DATABASE CHANGES HAVE BEEN MADE ***")
    print(f"\n  To review and approve these discoveries, run:")
    print(f"    python3 curate_db.py approve --report {report_path}")


# =====================================================================
# DISCOVERY MODE — PHASE 2: APPROVE (Human Reviews & Approves)
# =====================================================================

def run_approve(report_path: str) -> None:
    """
    Discovery Mode Phase 2: Human reviews the discovery report's reasoning
    and approves the suggested drugs.
    """
    if not os.path.exists(report_path):
        print(f"  ERROR: Report file not found: {report_path}")
        sys.exit(1)

    with open(report_path, "r") as f:
        report = json.load(f)

    disease_name = report.get("disease", "Unknown")
    mesh_id = report.get("mesh_id", "Unknown")
    status = report.get("status", "UNKNOWN")
    discoveries = report.get("discoveries", [])

    if status == "APPROVED":
        print(f"  This report has already been approved.")
        print(f"  Approved at: {report.get('approved_at', 'unknown')}")
        print(f"\n  Next steps:")
        found = [d for d in discoveries if d.get("db_status") == "FOUND_IN_DB"]
        not_found = [d for d in discoveries if d.get("db_status") == "NOT_IN_DB"]
        if found:
            print(f"    Map {len(found)} drug(s) to database:")
            print(f"      python3 curate_db.py map --report {report_path}")
        if not_found:
            print(f"    Simulate {len(not_found)} drug(s) first:")
            print(f"      python3 curate_db.py simulate --report {report_path}")
        return

    if status == "MAPPED":
        print(f"  This report has already been fully processed.")
        return

    print(f"\n{'#' * 60}")
    print(f"  CANDO Discovery Tool — Phase 2: APPROVE")
    print(f"  Disease: {disease_name} ({mesh_id})")
    print(f"  Report: {os.path.basename(report_path)}")
    print(f"{'#' * 60}\n")

    # Show the full discovery report with reasoning
    found_in_db, not_in_db = print_discovery_report(discoveries, disease_name)

    # Summary and confirmation
    if found_in_db:
        print(f"  The following {len(found_in_db)} drug(s) will be QUEUED FOR MAPPING")
        print(f"  (they already exist in the CANDO database):\n")
        for d in found_in_db:
            name = d.get("drug_name_display", d.get("drug_name", "Unknown"))
            cid = d.get("cando_id", "?")
            print(f"    +  {name} (CANDO ID: {cid})")

    if not_in_db:
        print(f"\n  The following {len(not_in_db)} drug(s) will be QUEUED FOR SIMULATION")
        print(f"  (they need docking before mapping):\n")
        for d in not_in_db:
            name = d.get("drug_name_display", d.get("drug_name", "Unknown"))
            print(f"    ⚙  {name}")

    print()
    try:
        confirm = input("  Do you approve these discoveries? Type 'yes' to confirm: ")
    except (EOFError, KeyboardInterrupt):
        confirm = ""

    if confirm.strip().lower() != "yes":
        print("  Aborted. No changes made.")
        return

    # Mark as approved
    report["status"] = "APPROVED"
    report["approved_at"] = datetime.now().isoformat()
    report["approved_by"] = "manual_approval"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n  ✓ Report approved successfully.")
    print(f"    Report marked as APPROVED: {report_path}")

    if found_in_db:
        print(f"\n  Next step — Map {len(found_in_db)} drug(s) to the database:")
        print(f"    python3 curate_db.py map --report {report_path}")
    if not_in_db:
        print(f"\n  Next step — Simulate {len(not_in_db)} drug(s) first:")
        print(f"    python3 curate_db.py simulate --report {report_path}")


# =====================================================================
# DISCOVERY MODE — PHASE 3: SIMULATE (Stub — Needs William's Pipeline)
# =====================================================================

def run_simulate(report_path: str) -> None:
    """
    Discovery Mode Phase 3: Trigger CANDO docking simulation for drugs
    that are NOT in the database.

    ⚠️ STUB — This requires integration with William's docking pipeline.
    Currently prints the simulation queue for manual processing.
    """
    if not os.path.exists(report_path):
        print(f"  ERROR: Report file not found: {report_path}")
        sys.exit(1)

    with open(report_path, "r") as f:
        report = json.load(f)

    status = report.get("status", "UNKNOWN")
    disease_name = report.get("disease", "Unknown")
    discoveries = report.get("discoveries", [])

    if status not in ("APPROVED", "SIMULATED"):
        print(f"  ERROR: Report must be approved first (current status: {status}).")
        print(f"  Run: python3 curate_db.py approve --report {report_path}")
        return

    not_in_db = [d for d in discoveries if d.get("db_status") == "NOT_IN_DB"]

    if not not_in_db:
        print(f"  No drugs need simulation. All discovered drugs are already in the CANDO database.")
        print(f"  Proceed directly to mapping:")
        print(f"    python3 curate_db.py map --report {report_path}")
        return

    print(f"\n{'#' * 60}")
    print(f"  CANDO Discovery Tool — Phase 3: SIMULATE")
    print(f"  Disease: {disease_name}")
    print(f"{'#' * 60}\n")

    print(f"  ⚠️  SIMULATION QUEUE")
    print(f"  The following {len(not_in_db)} drug(s) need molecular docking")
    print(f"  simulation before they can be added to the CANDO database:\n")

    for i, d in enumerate(not_in_db, start=1):
        name = d.get("drug_name_display", d.get("drug_name", "Unknown"))
        mechanism = d.get("mechanism", "")
        print(f"  [{i}] {name}")
        print(f"      Mechanism: {mechanism}")
        syns = d.get("synonyms_checked", [])
        if syns:
            print(f"      PubChem synonyms: {', '.join(syns[:3])}")
        print()

    print(f"  ─────────────────────────────────────────────────────────")
    print(f"  This step requires integration with the CANDO docking")
    print(f"  pipeline. Please process these drugs through your")
    print(f"  molecular simulation workflow manually.")
    print(f"")
    print(f"  Once the simulations are complete and the compounds")
    print(f"  have been added to the database, re-run:")
    print(f"    python3 curate_db.py map --report {report_path}")
    print(f"  ─────────────────────────────────────────────────────────")


# =====================================================================
# DISCOVERY MODE — PHASE 4: MAP (Add Approved Mappings to Database)
# =====================================================================

def run_map(report_path: str, dest_db: str = None) -> None:
    """
    Discovery Mode Phase 4: Add approved, FOUND_IN_DB drugs to the
    curated database as new drug-disease mappings.
    """
    if not os.path.exists(report_path):
        print(f"  ERROR: Report file not found: {report_path}")
        sys.exit(1)

    with open(report_path, "r") as f:
        report = json.load(f)

    disease_name = report.get("disease", "Unknown")
    mesh_id = report.get("mesh_id", "Unknown")
    status = report.get("status", "UNKNOWN")
    target_db = dest_db or report.get("target_database", DEFAULT_DEST_DB)
    discoveries = report.get("discoveries", [])

    if status not in ("APPROVED", "SIMULATED"):
        print(f"  ERROR: Report must be approved first (current status: {status}).")
        print(f"  Run: python3 curate_db.py approve --report {report_path}")
        return

    if status == "MAPPED":
        print(f"  This report has already been mapped to the database.")
        print(f"  Mapped at: {report.get('mapped_at', 'unknown')}")
        return

    found_in_db = [d for d in discoveries if d.get("db_status") == "FOUND_IN_DB"]
    not_in_db = [d for d in discoveries if d.get("db_status") == "NOT_IN_DB"]

    if not found_in_db:
        print(f"  No drugs are ready to map (all need simulation first).")
        if not_in_db:
            print(f"  Run simulation first:")
            print(f"    python3 curate_db.py simulate --report {report_path}")
        return

    print(f"\n{'#' * 60}")
    print(f"  CANDO Discovery Tool — Phase 4: MAP")
    print(f"  Disease: {disease_name} ({mesh_id})")
    print(f"  Report: {os.path.basename(report_path)}")
    print(f"  Target DB: {os.path.basename(target_db)}")
    print(f"{'#' * 60}\n")

    # Show what will be added
    print(f"  The following {len(found_in_db)} drug(s) will be")
    print(f"  ADDED as new mappings to {disease_name} in '{os.path.basename(target_db)}':\n")
    for d in found_in_db:
        name = d.get("drug_name_display", d.get("drug_name", "Unknown"))
        cid = d.get("cando_id", "?")
        cando_name = d.get("cando_name", "?")
        print(f"    +  {name} → {cando_name} (CANDO ID: {cid})")

    if not_in_db:
        print(f"\n  The following {len(not_in_db)} drug(s) will be SKIPPED (need simulation):\n")
        for d in not_in_db:
            name = d.get("drug_name_display", d.get("drug_name", "Unknown"))
            print(f"    ⚙  {name} (not in CANDO database)")

    print()
    try:
        confirm = input("  Do you approve adding these mappings? Type 'yes' to confirm: ")
    except (EOFError, KeyboardInterrupt):
        confirm = ""

    if confirm.strip().lower() != "yes":
        print("  Aborted. No changes made to the database.")
        return

    # Apply the new mappings
    if not os.path.exists(target_db):
        print(f"  ERROR: Target database not found: {target_db}")
        return

    added_count = 0
    skipped_count = 0
    for d in found_in_db:
        cando_id = d.get("cando_id")
        name = d.get("drug_name_display", d.get("drug_name", "Unknown"))
        if cando_id:
            was_added = add_compound_to_indication(target_db, mesh_id, cando_id)
            if was_added:
                print(f"    ✓  Added: {name} (ID: {cando_id})")
                added_count += 1
            else:
                print(f"    ─  Already mapped: {name} (ID: {cando_id})")
                skipped_count += 1

    # Mark the report as mapped
    report["status"] = "MAPPED"
    report["mapped_at"] = datetime.now().isoformat()
    report["mapped_by"] = "manual_approval"
    report["mapping_summary"] = {
        "added": added_count,
        "already_mapped": skipped_count,
        "needs_simulation": len(not_in_db)
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n  ✓ Database updated successfully.")
    print(f"    Added {added_count} new mapping(s) to {disease_name}.")
    if skipped_count:
        print(f"    Skipped {skipped_count} (already mapped).")
    if not_in_db:
        print(f"    {len(not_in_db)} drug(s) still need simulation.")
    print(f"    Curated database: {target_db}")
    print(f"    Report marked as MAPPED: {report_path}")


# =====================================================================
# CLI ENTRY POINT
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="CANDO Database Curation & Discovery Tool — Human-in-the-loop workflows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
MODE 1 — CURATE (Clean up false/misleading drug-disease mappings):

  Phase 1 — Generate a curation report (no DB changes):
    python3 curate_db.py curate --mesh-id MESH:D003550 --provider gemini

  Phase 2 — Review the report, then apply changes:
    python3 curate_db.py apply --report curation_reports/MESH_D003550.json

MODE 2 — DISCOVER (Find missing disease-modifying drugs):

  Phase 1 — LLM suggests missing drugs + cross-references CANDO DB:
    python3 curate_db.py discover --mesh-id MESH:D003550 --provider gemini

  Phase 2 — Review reasoning and approve:
    python3 curate_db.py approve --report discovery_reports/MESH_D003550_discovery.json

  Phase 3 — View simulation queue (for drugs not in CANDO DB):
    python3 curate_db.py simulate --report discovery_reports/MESH_D003550_discovery.json

  Phase 4 — Add approved mappings to the database:
    python3 curate_db.py map --report discovery_reports/MESH_D003550_discovery.json
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # ── Helper to add common LLM arguments to a parser ──
    def add_llm_args(p):
        p.add_argument("--mesh-id", required=True,
                       help="MeSH ID of the indication (e.g. MESH:D003550).")
        p.add_argument("--src-db", default=DEFAULT_SRC_DB,
                       help="Path to the original (read-only) CANDO database.")
        p.add_argument("--dest-db", default=DEFAULT_DEST_DB,
                       help="Path to the curated database copy.")
        p.add_argument("--url", default=None,
                       help="Base URL of the LLM server.")
        p.add_argument("--model", default=None,
                       help="Name of the LLM model.")
        p.add_argument("--provider", choices=["local", "gemini"], default="local",
                       help="LLM provider: 'local' or 'gemini'. Default: local")
        p.add_argument("--api-key", default=None,
                       help="API key (for Gemini, defaults to GEMINI_API_KEY env var).")
        p.add_argument("--reset", action="store_true",
                       help="Re-copy the source database and start fresh.")

    # ── MODE 1: curate ──
    curate_parser = subparsers.add_parser(
        "curate", help="[Mode 1] Cleanup: Gather evidence and generate a curation report.")
    add_llm_args(curate_parser)

    # ── MODE 1: apply ──
    apply_parser = subparsers.add_parser(
        "apply", help="[Mode 1] Cleanup: Apply a curation report to the database.")
    apply_parser.add_argument("--report", required=True,
                              help="Path to the curation report JSON.")
    apply_parser.add_argument("--dest-db", default=None,
                              help="Override the target database path.")

    # ── MODE 2: discover ──
    discover_parser = subparsers.add_parser(
        "discover", help="[Mode 2] Discovery: Find missing disease-modifying drugs.")
    add_llm_args(discover_parser)

    # ── MODE 2: approve ──
    approve_parser = subparsers.add_parser(
        "approve", help="[Mode 2] Discovery: Review and approve a discovery report.")
    approve_parser.add_argument("--report", required=True,
                                help="Path to the discovery report JSON.")

    # ── MODE 2: simulate ──
    simulate_parser = subparsers.add_parser(
        "simulate", help="[Mode 2] Discovery: View simulation queue for drugs not in CANDO DB.")
    simulate_parser.add_argument("--report", required=True,
                                 help="Path to the approved discovery report JSON.")

    # ── MODE 2: map ──
    map_parser = subparsers.add_parser(
        "map", help="[Mode 2] Discovery: Add approved drug mappings to the database.")
    map_parser.add_argument("--report", required=True,
                            help="Path to the approved discovery report JSON.")
    map_parser.add_argument("--dest-db", default=None,
                            help="Override the target database path.")

    args = parser.parse_args()

    # ── Resolve LLM provider for commands that need it ──
    def resolve_llm_args(args):
        if args.provider == "gemini":
            llm_url = args.url or GEMINI_API_URL
            llm_model = args.model or GEMINI_DEFAULT_MODEL
            api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                print("ERROR: Gemini provider requires an API key.")
                print("  Set GEMINI_API_KEY in your .env file or pass --api-key.")
                sys.exit(1)
        else:
            llm_url = args.url or DEFAULT_LLM_URL
            llm_model = args.model or DEFAULT_LLM_MODEL
            api_key = args.api_key
        return llm_url, llm_model, api_key

    if args.command == "curate":
        llm_url, llm_model, api_key = resolve_llm_args(args)
        run_curate(
            mesh_id=args.mesh_id,
            src_db=args.src_db,
            dest_db=args.dest_db,
            llm_url=llm_url,
            llm_model=llm_model,
            api_key=api_key,
            reset=args.reset
        )
    elif args.command == "apply":
        run_apply(
            report_path=args.report,
            dest_db=args.dest_db
        )
    elif args.command == "discover":
        llm_url, llm_model, api_key = resolve_llm_args(args)
        run_discover(
            mesh_id=args.mesh_id,
            src_db=args.src_db,
            dest_db=args.dest_db,
            llm_url=llm_url,
            llm_model=llm_model,
            api_key=api_key,
            reset=args.reset
        )
    elif args.command == "approve":
        run_approve(report_path=args.report)
    elif args.command == "simulate":
        run_simulate(report_path=args.report)
    elif args.command == "map":
        run_map(
            report_path=args.report,
            dest_db=args.dest_db
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
