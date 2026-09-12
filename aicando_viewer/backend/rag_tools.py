"""
AICANDO RAG Tools — Grounded Retrieval-Augmented Generation Pipelines

Provides three RAG tool pipelines for the AICANDO drug-repurposing interface:
  1. Literature Review   — PubMed abstract retrieval + pharmacological adjudication
  2. Compound Lookup     — SQLite-based compound similarity + disease association analysis
  3. Protein Screening   — Docking matrix target extraction + pathway analysis

Architecture:
  - Each tool follows the same pattern: RETRIEVE → FORMAT → ADJUDICATE → RETURN
  - Data retrieval is separated from prompt construction for testability
  - Prompts are grounded: the LLM may only cite data provided in the dossier
  - All outputs conform to structured JSON schemas for frontend rendering

Integration:
  When used with a local LLM (e.g., William's 120B model), each public function
  in this module is registered as a callable tool. The LLM reads the function
  docstrings and decides which tool to invoke based on the user query.
  When used with an API-based LLM (e.g., Gemini), the FastAPI layer in main.py
  calls these functions directly based on the request endpoint.
"""

import os
import json
import time
import sqlite3
import requests
import pandas as pd
from typing import List, Tuple, Dict, Optional


# =====================================================================
# CONFIGURATION
# =====================================================================

# Resolve paths relative to the CANDO project root
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_AICANDO_DIR = os.path.dirname(_BACKEND_DIR)
_CANDO_ROOT = os.path.dirname(_AICANDO_DIR)

# Database and data file paths
DB_PATH = os.path.join(_CANDO_ROOT, "cando-tutorial_matrix-approved-cmpds-v2.2-cmpds2inds-v2.2.db")
TUTORIAL_DIR = os.path.join(_CANDO_ROOT, "tutorial")
CMPD_MAP_FILE = os.path.join(TUTORIAL_DIR, "cmpds-v2.2.tsv")
IND_MAP_FILE = os.path.join(TUTORIAL_DIR, "cmpds2inds-v2.2.tsv")

# Build MeSH ID to English disease name lookup at import time
_mesh_to_english: Dict[str, str] = {}
try:
    _ind_df = pd.read_csv(IND_MAP_FILE, sep='\t')
    for _, row in _ind_df[['INDICATION_NAME', 'MESH_ID']].drop_duplicates().iterrows():
        _mesh_to_english[str(row['MESH_ID'])] = str(row['INDICATION_NAME'])
except Exception:
    pass


def _resolve_disease_name(mesh_id: str) -> str:
    """Resolve a MeSH ID to its English disease name."""
    prefixed = mesh_id if mesh_id.startswith("MESH:") else f"MESH:{mesh_id}"
    return _mesh_to_english.get(prefixed, _mesh_to_english.get(mesh_id, mesh_id))


# =====================================================================
# 1. DATABASE UTILITIES — SQLite queries for compounds, indications,
#    and precomputed cosine similarity distances
# =====================================================================

def _get_db_connection() -> sqlite3.Connection:
    """Open a read-only connection to the CANDO SQLite database."""
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"CANDO database not found at {DB_PATH}")
    return sqlite3.connect(DB_PATH)


def search_compounds(name_query: str, limit: int = 10) -> List[Dict]:
    """
    Fuzzy-search the compound database by name substring match.

    Args:
        name_query: Search term (e.g., "ciprofloxacin", "tamoxifen").
        limit: Maximum number of results to return.

    Returns:
        List of dicts with keys: id, name.
    """
    conn = _get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name FROM cmpds WHERE LOWER(name) LIKE ? LIMIT ?",
        (f"%{name_query.lower().strip()}%", limit)
    )
    results = [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
    conn.close()
    return results


def get_compound_details(compound_id: str) -> Optional[Dict]:
    """
    Retrieve full metadata for a single compound by CANDO ID.

    Args:
        compound_id: The CANDO compound ID (e.g., "421").

    Returns:
        Dict with keys: id, name, associated_diseases (list of dicts with
        mesh_id and disease_name), or None if not found.
    """
    conn = _get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, name, ind_ids FROM cmpds WHERE id = ?", (str(compound_id),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    cmpd_id, cmpd_name, ind_ids_str = row

    # Parse the stringified list of MeSH IDs
    diseases = []
    try:
        ind_ids = eval(ind_ids_str)  # stored as Python list literal
        for mesh_id in ind_ids:
            diseases.append({
                "mesh_id": mesh_id,
                "disease_name": _resolve_disease_name(mesh_id)
            })
    except Exception:
        pass

    conn.close()
    return {
        "id": cmpd_id,
        "name": cmpd_name,
        "associated_diseases": diseases
    }


def get_compound_similarities(compound_id: str, top_n: int = 10) -> List[Dict]:
    """
    Retrieve the top-N most similar compounds by precomputed cosine distance.

    Args:
        compound_id: The CANDO compound ID.
        top_n: Number of similar compounds to return.

    Returns:
        Sorted list of dicts with keys: id, name, distance (lower = more similar).
    """
    conn = _get_db_connection()
    cursor = conn.cursor()

    # Fetch precomputed distances
    cursor.execute("SELECT dists FROM cosine WHERE id = ?", (str(compound_id),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return []

    # Parse the stringified dictionary (uses single quotes)
    dists_dict = json.loads(row[0].replace("'", '"'))

    # Sort by distance ascending and take top N
    sorted_pairs = sorted(dists_dict.items(), key=lambda x: x[1])[:top_n]

    # Enrich with compound names
    results = []
    for other_id, distance in sorted_pairs:
        cursor.execute("SELECT name FROM cmpds WHERE id = ?", (str(other_id),))
        name_row = cursor.fetchone()
        results.append({
            "id": other_id,
            "name": name_row[0] if name_row else f"compound_{other_id}",
            "distance": round(distance, 6)
        })

    conn.close()
    return results


def get_indication_compounds(mesh_id: str) -> List[Dict]:
    """
    Retrieve all compounds associated with a disease indication.

    Args:
        mesh_id: MeSH ID (e.g., "MESH:D001943" for Breast Neoplasms).

    Returns:
        List of dicts with keys: id, name.
    """
    prefixed = mesh_id if mesh_id.startswith("MESH:") else f"MESH:{mesh_id}"
    conn = _get_db_connection()
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


# =====================================================================
# 2. PUBMED RETRIEVAL — Shared utility for fetching abstracts from NCBI
# =====================================================================

def fetch_pubmed_abstracts(
    drug_name: str,
    disease_name: str,
    max_results: int = 3
) -> Tuple[List[Dict], List[str]]:
    """
    Retrieve abstracts from the NCBI PubMed E-utilities API.

    Args:
        drug_name: Name of the drug compound.
        disease_name: English name of the target disease.
        max_results: Maximum number of abstracts to retrieve.

    Returns:
        Tuple of (structured_abstracts, paper_urls).
        structured_abstracts: list of dicts with keys: index, pmid, url, text.
        paper_urls: flat list of PubMed URLs for frontend rendering.
    """
    try:
        search_term = f"{drug_name}+{disease_name}".replace(" ", "+")
        search_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&term={search_term}&retmode=json&retmax={max_results}"
        )
        search_resp = requests.get(search_url, timeout=10).json()
        id_list = search_resp.get('esearchresult', {}).get('idlist', [])

        if not id_list:
            fallback_url = f"https://pubmed.ncbi.nlm.nih.gov/?term={search_term}"
            return [], [fallback_url]

        structured_abstracts = []
        paper_urls = []

        for i, pmid in enumerate(id_list, start=1):
            paper_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            paper_urls.append(paper_url)

            fetch_url = (
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                f"?db=pubmed&id={pmid}&retmode=text&rettype=abstract"
            )
            abstract_text = requests.get(fetch_url, timeout=10).text.strip()

            structured_abstracts.append({
                "index": i,
                "pmid": pmid,
                "url": paper_url,
                "text": abstract_text
            })
            time.sleep(0.35)  # NCBI rate-limit compliance

        return structured_abstracts, paper_urls

    except Exception as e:
        print(f"ERROR: PubMed API request failed for {drug_name}: {e}")
        return [], [f"https://pubmed.ncbi.nlm.nih.gov/?term={drug_name}"]


# =====================================================================
# 3. RAG TOOL: LITERATURE REVIEW
#    Retrieves PubMed abstracts for CANDO-predicted drugs and runs
#    grounded pharmacological adjudication.
# =====================================================================

def _build_literature_dossier(drug_evidence: Dict[str, List[Dict]]) -> str:
    """Format retrieved abstracts into a numbered dossier for the LLM prompt."""
    dossier = ""
    for drug_name, abstracts in drug_evidence.items():
        dossier += f"\n{'=' * 40}\n"
        dossier += f"DRUG: {drug_name}\n"
        dossier += f"{'=' * 40}\n"

        if not abstracts:
            dossier += "  [No PubMed abstracts found for this drug-disease pair.]\n"
            continue

        for ab in abstracts:
            dossier += f"\n  Abstract {ab['index']} (PMID: {ab['pmid']}):\n"
            dossier += f"  {ab['text']}\n"

    return dossier


def literature_review(drug_list: List[Dict], target_mesh: str) -> Dict:
    """
    Run the Literature Review RAG pipeline on a list of CANDO-predicted drugs.

    Retrieves PubMed abstracts for each drug-disease pair and applies grounded
    pharmacological adjudication rules (class exclusions, mechanism-direction
    verification, evidence-tier classification) to produce recommendations.

    Args:
        drug_list: List of dicts, each with at least a "compound" key
                   (e.g., [{"compound": "Tamoxifen", "rank": 1}, ...]).
        target_mesh: MeSH ID of the target disease (e.g., "D001943").

    Returns:
        Dict containing top_candidates, watch_list, rejected, and metadata.
    """
    curated_drugs = drug_list[:30]
    disease_english = _resolve_disease_name(target_mesh)

    # Retrieve abstracts for every candidate
    drug_evidence: Dict[str, List[Dict]] = {}
    url_mapping: Dict[str, List[str]] = {}

    for drug in curated_drugs:
        name = drug['compound']
        abstracts, urls = fetch_pubmed_abstracts(name, disease_english)
        drug_evidence[name] = abstracts
        url_mapping[name] = urls

    literature_dossier = _build_literature_dossier(drug_evidence)

    prompt = f"""You are a Senior Oncology Pharmacologist AI Agent embedded in the CANDO drug-repurposing pipeline at the Samudrala Lab (University at Buffalo). Your role is reading-comprehension and pharmacological adjudication, NOT open-ended reasoning. You will be evaluated on precision, not recall: a confident wrong answer is far worse than an empty array.

SECTION 1 — UPSTREAM CONTEXT
Disease Target: MeSH ID {target_mesh} ({disease_english}).

The candidates below were produced by CANDO, a shotgun multitarget repurposing platform that:
  (a) computes a drug-proteome interaction signature for ~8,000 human-ingestible compounds across ~10,000-46,784 protein structures using bioinformatic docking;
  (b) ranks candidates by how closely their proteomic signature matches drugs already approved for the target indication.

CRITICAL CAVEAT: CANDO's signal is STRUCTURAL, not PHARMACODYNAMIC. A high-ranking match means "this drug binds a similar set of proteins as known cures." It does NOT distinguish agonist vs antagonist, therapeutic vs pathological pathway, or causative vs curative agent. Your job is to use the PubMed evidence below to determine the DIRECTION of action.

SECTION 2 — EVIDENCE DOSSIER (PRIMARY GROUND TRUTH)
The following are real abstracts retrieved from NCBI PubMed. Each abstract is your ONLY source of truth. You MUST NOT use prior knowledge to fill gaps.

{literature_dossier}

SECTION 3 — ADJUDICATION RULES (APPLY IN ORDER)
RULE 1: RELEVANCE GATE — Verify abstracts actually study the drug FOR {disease_english}.
RULE 2: DRUG-CLASS EXCLUSIONS — Reject hormonal agonists driving the disease, supportive-care-only agents, and nutritional/diagnostic agents (with documented exceptions).
RULE 3: MECHANISM-DIRECTION VERIFICATION — Locate a curative verb-phrase in the abstracts (e.g., "inhibits", "suppresses", "induces apoptosis"). Reject if only disqualifying verbs found.
RULE 4: EVIDENCE-TIER CLASSIFICATION — Tier A (Phase II/III), Tier B (Phase I or multiple preclinical), Tier C (single in vitro), Tier D (observational). Only recommend Tier A-C.
RULE 5: CONSENSUS BONUS — Mark consensus:true if 2+ abstracts corroborate the mechanism.
RULE 6: EMPTY-SET MANDATE — If zero drugs survive, return empty recommendations array.
RULE 7: SPECIFICITY PREFERENCE — Prefer targeted agents over broad-spectrum cytotoxics.

SECTION 4 — OUTPUT SCHEMA
Return a single JSON object with this exact shape. No markdown fences. Pure JSON only.

{{
  "disease": "{disease_english}",
  "mesh_id": "{target_mesh}",
  "recommendations": [
    {{
      "compound": "DrugName",
      "mechanism": "Brief mechanism of action.",
      "evidence_tier": "B",
      "consensus": true,
      "supporting_abstract_indices": [1, 2],
      "key_quoted_evidence": "Abstract 1: 'exact quote'.",
      "llm_summary": "Two-sentence summary for the UI."
    }}
  ],
  "watch_list": [
    {{
      "compound": "DrugName",
      "reason": "Tier D evidence only."
    }}
  ],
  "rejected": [
    {{
      "compound": "DrugName",
      "rule_violated": "Rule 2a",
      "reason": "Progestin; excluded as hormone-receptor agonist."
    }}
  ],
  "dossier_quality_note": "Optional quality flag."
}}

Return at most 3 recommendations. Return ALL evaluated drugs across arrays. Output raw JSON only."""

    return {
        "tool": "literature_review",
        "prompt": prompt,
        "drug_evidence": drug_evidence,
        "url_mapping": url_mapping,
        "disease_name": disease_english,
        "mesh_id": target_mesh,
        "curated_drugs": curated_drugs
    }


# =====================================================================
# 4. RAG TOOL: COMPOUND LOOKUP
#    Queries the CANDO SQLite database for compound similarity profiles
#    and disease associations, then runs grounded analysis.
# =====================================================================

def _build_compound_dossier(
    query_compound: Dict,
    similar_compounds: List[Dict],
    target_disease: str
) -> str:
    """Format compound similarity data into a structured dossier for the LLM."""
    dossier = f"{'=' * 50}\n"
    dossier += f"QUERY COMPOUND: {query_compound['name']} (CANDO ID: {query_compound['id']})\n"
    dossier += f"{'=' * 50}\n"

    if query_compound.get("associated_diseases"):
        dossier += "\nKnown Disease Associations:\n"
        for disease in query_compound["associated_diseases"]:
            dossier += f"  - {disease['disease_name']} ({disease['mesh_id']})\n"
    else:
        dossier += "\nNo known disease associations in the database.\n"

    dossier += f"\n{'=' * 50}\n"
    dossier += f"TOP SIMILAR COMPOUNDS (by proteomic signature cosine distance)\n"
    dossier += f"{'=' * 50}\n"
    dossier += f"{'Rank':<6}{'CANDO ID':<12}{'Name':<30}{'Distance':<12}\n"
    dossier += f"{'-'*60}\n"

    for rank, cmpd in enumerate(similar_compounds, start=1):
        dossier += f"{rank:<6}{cmpd['id']:<12}{cmpd['name']:<30}{cmpd['distance']:<12}\n"

    return dossier


def compound_lookup(compound_query: str, target_mesh: str = "", top_n: int = 10) -> Dict:
    """
    Run the Compound Lookup RAG pipeline for a drug name or CANDO ID.

    Retrieves compound metadata, disease associations, and proteomic similarity
    neighbors from the CANDO SQLite database. Constructs a grounded prompt for
    the LLM to analyze repurposing potential.

    Args:
        compound_query: Compound name (fuzzy search) or CANDO ID (exact lookup).
        target_mesh: Optional MeSH ID to focus the analysis on a specific disease.
        top_n: Number of similar compounds to include in the dossier.

    Returns:
        Dict containing the constructed prompt, retrieved data, and metadata.
    """
    # Determine if query is an ID or a name
    if compound_query.isdigit():
        query_compound = get_compound_details(compound_query)
    else:
        matches = search_compounds(compound_query, limit=1)
        if matches:
            query_compound = get_compound_details(matches[0]["id"])
        else:
            query_compound = None

    if not query_compound:
        return {
            "tool": "compound_lookup",
            "error": f"Compound '{compound_query}' not found in the CANDO database.",
            "prompt": None
        }

    similar_compounds = get_compound_similarities(query_compound["id"], top_n=top_n)
    disease_english = _resolve_disease_name(target_mesh) if target_mesh else "general repurposing"

    compound_dossier = _build_compound_dossier(query_compound, similar_compounds, disease_english)

    prompt = f"""You are a Lead Computational Chemist AI Agent embedded in the CANDO drug-repurposing pipeline at the Samudrala Lab (University at Buffalo). Your role is to analyze compound similarity profiles and disease associations from the CANDO database. You may ONLY cite data provided in the dossier below.

SECTION 1 — ANALYSIS CONTEXT
Query Compound: {query_compound['name']} (CANDO ID: {query_compound['id']})
Target Disease: {disease_english}
Analysis Type: Proteomic signature similarity and disease association mapping.

CANDO computes a drug-proteome interaction signature for each compound across thousands of protein structures. Compounds with similar signatures bind similar sets of proteins, suggesting shared mechanisms. Low cosine distance = high similarity.

SECTION 2 — COMPOUND DOSSIER (PRIMARY GROUND TRUTH)
The following data was retrieved from the CANDO SQLite database. This is your ONLY source of truth. Do NOT introduce external knowledge about these compounds.

{compound_dossier}

SECTION 3 — ANALYSIS INSTRUCTIONS
1. SIGNATURE NEIGHBORS: Identify the closest proteomic neighbors (distance < 0.05 indicates strong similarity). Note any clusters of related drugs.
2. DISEASE OVERLAP: Check if any similar compounds share disease associations with the query compound or the target disease.
3. REPURPOSING SIGNAL: If multiple neighbors are approved for the same disease that the query compound is NOT approved for, this is a repurposing signal.
4. DIRECTION-BLIND WARNING: Remind the researcher that CANDO similarity is structural. The query compound may act as agonist or antagonist at the shared targets.

SECTION 4 — OUTPUT SCHEMA
Return a single JSON object. No markdown fences. Pure JSON only.

{{
  "query_compound": "{query_compound['name']}",
  "cando_id": "{query_compound['id']}",
  "target_disease": "{disease_english}",
  "closest_neighbors": [
    {{
      "compound": "name",
      "cando_id": "id",
      "distance": 0.024,
      "shared_disease_associations": ["Disease A", "Disease B"],
      "note": "Brief mechanistic note based on disease overlap."
    }}
  ],
  "repurposing_signals": [
    {{
      "disease": "Disease Name",
      "supporting_compounds": ["Drug A", "Drug B"],
      "signal_strength": "STRONG|MODERATE|WEAK",
      "rationale": "Brief explanation."
    }}
  ],
  "summary": "Two-sentence summary of the compound's proteomic profile and repurposing potential."
}}

Output raw JSON only."""

    return {
        "tool": "compound_lookup",
        "prompt": prompt,
        "query_compound": query_compound,
        "similar_compounds": similar_compounds,
        "disease_name": disease_english,
        "mesh_id": target_mesh
    }


# =====================================================================
# 5. RAG TOOL: PROTEIN TARGET SCREENING
#    Identifies the top protein targets for a given compound using the
#    raw docking matrix, or screens compounds for a given protein target.
# =====================================================================

def _load_docking_matrix_for_compound(compound_index: int) -> List[Dict]:
    """
    Extract docking scores for a single compound from the raw TSV matrix.

    Reads column `compound_index` from each row of the matrix file.
    Returns a list of (protein_id, score) sorted by score descending.
    """
    matrix_file = os.path.join(TUTORIAL_DIR, "tutorial_matrix-approved.tsv")
    if not os.path.exists(matrix_file):
        return []

    interactions = []
    with open(matrix_file, 'r') as f:
        for line in f:
            parts = line.strip().split('\t')
            if len(parts) <= compound_index + 1:
                continue
            protein_id = parts[0]
            try:
                score = float(parts[compound_index + 1])
            except (ValueError, IndexError):
                continue
            interactions.append({"protein_id": protein_id, "score": score})

    interactions.sort(key=lambda x: x["score"], reverse=True)
    return interactions


def _build_protein_dossier(
    compound_name: str,
    compound_id: str,
    top_targets: List[Dict]
) -> str:
    """Format protein target interaction data into a dossier for the LLM."""
    dossier = f"{'=' * 50}\n"
    dossier += f"COMPOUND: {compound_name} (CANDO ID: {compound_id})\n"
    dossier += f"TOP PROTEIN TARGETS (by docking interaction score)\n"
    dossier += f"{'=' * 50}\n"
    dossier += f"{'Rank':<6}{'Protein ID':<14}{'Score':<10}\n"
    dossier += f"{'-'*30}\n"

    for rank, target in enumerate(top_targets, start=1):
        dossier += f"{rank:<6}{target['protein_id']:<14}{target['score']:<10.4f}\n"

    return dossier


def protein_screening(compound_query: str, top_n: int = 15) -> Dict:
    """
    Run the Protein Target Screening RAG pipeline for a given compound.

    Reads the raw docking matrix to identify the top protein structures
    showing the strongest interaction scores, then constructs a grounded
    prompt for the LLM to perform biological pathway analysis.

    Args:
        compound_query: Compound name (fuzzy search) or CANDO ID (exact lookup).
        top_n: Number of top protein targets to include.

    Returns:
        Dict containing the constructed prompt, retrieved data, and metadata.
    """
    # Resolve compound
    if compound_query.isdigit():
        query_compound = get_compound_details(compound_query)
        compound_index = int(compound_query)
    else:
        matches = search_compounds(compound_query, limit=1)
        if matches:
            query_compound = get_compound_details(matches[0]["id"])
            compound_index = int(matches[0]["id"])
        else:
            query_compound = None
            compound_index = -1

    if not query_compound:
        return {
            "tool": "protein_screening",
            "error": f"Compound '{compound_query}' not found in the CANDO database.",
            "prompt": None
        }

    all_targets = _load_docking_matrix_for_compound(compound_index)
    top_targets = all_targets[:top_n]

    if not top_targets:
        return {
            "tool": "protein_screening",
            "error": "Docking matrix not available or compound index out of range.",
            "prompt": None
        }

    protein_dossier = _build_protein_dossier(
        query_compound["name"], query_compound["id"], top_targets
    )

    prompt = f"""You are a Molecular Biophysicist AI Agent embedded in the CANDO drug-repurposing pipeline at the Samudrala Lab (University at Buffalo). Your role is to analyze compound-protein docking interaction scores and identify biologically significant binding targets. You may ONLY cite data provided in the dossier below.

SECTION 1 — ANALYSIS CONTEXT
Compound: {query_compound['name']} (CANDO ID: {query_compound['id']})
Analysis Type: Top protein target identification from docking interaction scores.

CANDO computes interaction scores between each compound and thousands of protein structures using bioinformatic docking (BSscore + OBscore). Higher scores indicate stronger predicted binding. The protein IDs are PDB structure identifiers with chain suffixes (e.g., "2xpwA" = PDB 2xpw, chain A).

SECTION 2 — PROTEIN TARGET DOSSIER (PRIMARY GROUND TRUTH)
The following data was extracted from the CANDO docking matrix. This is your ONLY source of truth. Do NOT introduce external knowledge about protein functions.

{protein_dossier}

SECTION 3 — ANALYSIS INSTRUCTIONS
1. TOP BINDING PARTNERS: Identify the proteins with the highest interaction scores. Scores above 0.5 indicate notably strong binding predictions.
2. PDB STRUCTURE MAPPING: For each top target, note the PDB ID and chain. If you recognize the protein from the PDB ID, state its known function, but clearly mark it as external context.
3. SCORE DISTRIBUTION: Comment on whether the scores show a clear top cluster vs. a gradual distribution, which affects confidence.
4. DIRECTION-BLIND WARNING: Remind the researcher that docking scores measure binding strength, NOT functional effect. Strong binding could mean inhibition OR activation.

SECTION 4 — OUTPUT SCHEMA
Return a single JSON object. No markdown fences. Pure JSON only.

{{
  "compound": "{query_compound['name']}",
  "cando_id": "{query_compound['id']}",
  "top_targets": [
    {{
      "protein_id": "2xpwA",
      "score": 0.760,
      "pdb_id": "2xpw",
      "chain": "A",
      "note": "Strongest binding prediction. Biological function requires independent validation."
    }}
  ],
  "score_distribution_note": "Brief comment on score patterns.",
  "validation_recommendations": [
    "Suggest specific follow-up experiments or literature searches."
  ],
  "summary": "Two-sentence summary of the compound's target profile."
}}

Output raw JSON only."""

    return {
        "tool": "protein_screening",
        "prompt": prompt,
        "query_compound": query_compound,
        "top_targets": top_targets,
        "all_target_count": len(all_targets)
    }


# =====================================================================
# 6. TOOL REGISTRY — Provides metadata for LLM tool-calling integration
# =====================================================================

TOOL_REGISTRY = [
    {
        "name": "literature_review",
        "description": (
            "Retrieves PubMed abstracts for a list of CANDO-predicted drug candidates "
            "and runs grounded pharmacological adjudication to determine which drugs have "
            "evidence supporting therapeutic use for the target disease. Use this tool when "
            "the user asks about literature evidence, clinical trials, or PubMed validation "
            "for predicted drugs."
        ),
        "parameters": {
            "drug_list": "List of drug dicts with 'compound' key",
            "target_mesh": "MeSH ID of the target disease"
        },
        "function": literature_review
    },
    {
        "name": "compound_lookup",
        "description": (
            "Queries the CANDO database for a compound's proteomic similarity profile "
            "and disease associations. Returns the most similar drugs by docking signature "
            "and identifies repurposing signals. Use this tool when the user asks about a "
            "specific drug, its similar compounds, or its disease associations."
        ),
        "parameters": {
            "compound_query": "Drug name or CANDO ID",
            "target_mesh": "Optional MeSH ID to focus analysis",
            "top_n": "Number of similar compounds to return"
        },
        "function": compound_lookup
    },
    {
        "name": "protein_screening",
        "description": (
            "Reads the CANDO docking matrix to identify the top protein targets for a "
            "given compound based on interaction scores. Use this tool when the user asks "
            "about a drug's protein targets, binding partners, or molecular interactions."
        ),
        "parameters": {
            "compound_query": "Drug name or CANDO ID",
            "top_n": "Number of top targets to return"
        },
        "function": protein_screening
    }
]
