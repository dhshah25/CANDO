import os
import io
import json
import time
import pandas as pd
import requests
from typing import List, Tuple, Dict
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

import google.generativeai as genai

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

app = FastAPI(title="CANDO LitReview RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY not found in environment.")
else:
    genai.configure(api_key=GEMINI_API_KEY)


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tutorial_dir = os.path.join(BASE_DIR, "tutorial")

# MeSH ID to English disease name lookup table
_mesh_to_english = {}
try:
    _ind_df = pd.read_csv(os.path.join(tutorial_dir, 'cmpds2inds-v2.2.tsv'), sep='\t')
    for _, row in _ind_df[['INDICATION_NAME', 'MESH_ID']].drop_duplicates().iterrows():
        _mesh_to_english[str(row['MESH_ID'])] = str(row['INDICATION_NAME'])
except Exception as e:
    print(f"WARNING: Could not load disease name mappings: {e}")


def parse_prediction_file(file_bytes: bytes, filename: str) -> List[dict]:
    """Parse a TSV or CSV prediction file uploaded by the user."""
    try:
        content = io.BytesIO(file_bytes)

        if filename.endswith('.csv'):
            df = pd.read_csv(content)
        else:
            df = pd.read_csv(content, sep='\t')

        name_col = None
        for candidate in ['name', 'compound', 'drug', 'compound_name', 'drug_name']:
            if candidate in df.columns:
                name_col = candidate
                break

        if name_col is None:
            raise ValueError(f"Could not find a compound name column. Available columns: {list(df.columns)}")

        rank_col = None
        for candidate in ['rank', 'score1', 'score', 'probability']:
            if candidate in df.columns:
                rank_col = candidate
                break

        results = []
        for idx, row in df.head(30).iterrows():
            drug_name = str(row[name_col]).strip().title()
            results.append({
                "compound": drug_name,
                "rank": int(row[rank_col]) if rank_col else (idx + 1)
            })


        return results

    except Exception as e:
        raise ValueError(f"Failed to parse prediction file: {str(e)}")



def fetch_real_pubmed_data(drug_name: str, disease_name: str) -> Tuple[List[Dict], List[str]]:
    """
    Hits the official PubMed API to get up to 3 abstracts.
    Returns:
      - structured_abstracts: list of {index, pmid, url, text} dicts so the LLM
        can cite "Abstract 1", "Abstract 2" and the frontend can map back to URLs.
      - paper_urls: flat list of URLs for the UI.
    """
    try:
        search_term = f"{drug_name}+{disease_name}".replace(" ", "+")
        search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={search_term}&retmode=json&retmax=3"
        search_resp = requests.get(search_url).json()

        id_list = search_resp.get('esearchresult', {}).get('idlist', [])

        if not id_list:
            fallback_url = f"https://pubmed.ncbi.nlm.nih.gov/?term={search_term}"
            return [], [fallback_url]

        structured_abstracts = []
        paper_urls = []

        for i, pmid in enumerate(id_list, start=1):
            paper_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            paper_urls.append(paper_url)

            fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=text&rettype=abstract"
            abstract_text = requests.get(fetch_url).text.strip()

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


def build_literature_dossier(drug_evidence: Dict[str, List[Dict]]) -> str:
    """
    Format the dossier so the LLM can cite by 'Abstract N' precisely.
    The refined prompt's quoted-verb test depends on this indexing.
    """
    dossier = ""
    for drug_name, abstracts in drug_evidence.items():
        dossier += f"\n{'═' * 40}\n"
        dossier += f"DRUG: {drug_name}\n"
        dossier += f"{'═' * 40}\n"

        if not abstracts:
            dossier += "  [No PubMed abstracts found for this drug-disease pair.]\n"
            continue

        for ab in abstracts:
            dossier += f"\n  Abstract {ab['index']} (PMID: {ab['pmid']}):\n"
            dossier += f"  {ab['text']}\n"

    return dossier


def llm_pubmed_summarize(drug_list: List[dict], target_mesh: str) -> dict:
    """
    Refined Grounded RAG Pipeline using the structured prompt + schema.
    Citation-grounded, class-based exclusions, quoted-verb test,
    four-tier evidence schema, full audit trail (recommend / watch / reject).
    """
    curated_drugs = drug_list[:30]

    # Resolve MeSH ID to English disease name (try both prefixed and bare forms)
    disease_english = _mesh_to_english.get(f"MESH:{target_mesh}", target_mesh)
    if disease_english == target_mesh:
        disease_english = _mesh_to_english.get(target_mesh, target_mesh)

    # Retrieve abstracts for every candidate, keep them structured
    drug_evidence: Dict[str, List[Dict]] = {}
    url_mapping: Dict[str, List[str]] = {}

    for drug in curated_drugs:
        name = drug['compound']
        structured_abstracts, paper_urls = fetch_real_pubmed_data(name, disease_english)
        drug_evidence[name] = structured_abstracts
        url_mapping[name] = paper_urls

    literature_dossier = build_literature_dossier(drug_evidence)

    # Grounded RAG prompt: citation-grounded adjudication with
    # class-based exclusions, quoted-verb test, four-tier evidence
    # schema, and full audit trail (recommend / watch / reject).
    prompt = f"""You are a Senior Oncology Pharmacologist AI Agent embedded in the CANDO drug-repurposing pipeline at the Samudrala Lab (University at Buffalo). Your role is reading-comprehension and pharmacological adjudication, NOT open-ended reasoning. You will be evaluated on precision, not recall: a confident wrong answer is far worse than an empty array.

═══════════════════════════════════════════════════════════════════════
SECTION 1 — UPSTREAM CONTEXT (HOW THE CANDIDATES WERE GENERATED)
═══════════════════════════════════════════════════════════════════════
Disease Target: MeSH ID {target_mesh} ({disease_english}).

The candidates below were produced by CANDO, a shotgun multitarget repurposing platform that:
  (a) computes a drug-proteome interaction signature for ~8,000 human-ingestible compounds across ~10,000–46,784 protein structures using bioinformatic docking (BSscore + OBscore, scored by RMSD similarity);
  (b) ranks candidates by how closely their proteomic signature matches drugs already approved for the target indication.

CRITICAL CAVEAT — CANDO's signal is STRUCTURAL, not PHARMACODYNAMIC.
A high-ranking CANDO match means "this drug binds a similar set of proteins as known cures." It does NOT distinguish:
   • AGONIST vs ANTAGONIST at those proteins  ← drug may DRIVE the disease
   • Therapeutic vs pathological pathway      ← drug may worsen, not heal
   • Causative agent vs curative agent        ← signature similarity is direction-blind
Your job is to use the PubMed evidence below to determine the DIRECTION of action.

═══════════════════════════════════════════════════════════════════════
SECTION 2 — THE EVIDENCE DOSSIER (PRIMARY GROUND TRUTH)
═══════════════════════════════════════════════════════════════════════
The following are real abstracts retrieved from NCBI PubMed via E-utilities. Each abstract is your ONLY source of truth. You MUST NOT use prior knowledge to fill gaps. If a claim is not in these abstracts, it does not exist for the purpose of this task. When you cite evidence, refer to abstracts by their numbered label (e.g., "Abstract 1", "Abstract 2") exactly as they appear below.

{literature_dossier}

═══════════════════════════════════════════════════════════════════════
SECTION 3 — ADJUDICATION RULES (APPLY IN ORDER, STOP AT FIRST FAIL)
═══════════════════════════════════════════════════════════════════════

RULE 1 — RELEVANCE GATE.
Before anything else, verify each abstract is actually STUDYING the drug FOR {disease_english}, not merely mentioning the disease in passing (e.g., as a comorbidity, a control group, or an unrelated case report). If 0 of the retrieved abstracts directly investigate the drug-disease pair, the drug FAILS at Rule 1.

RULE 2 — DRUG-CLASS EXCLUSIONS (CLASS-BASED, NOT NAME-BASED).
Reject any drug whose primary mechanism falls into these classes, regardless of CANDO score or any "association with survival" in observational data:

  (a) HORMONAL AGONISTS at receptors that DRIVE the target disease.
      For hormone-receptor-positive cancers (breast, endometrial, prostate, ovarian):
        — reject ALL progestins, estrogens, androgens, and their precursors/derivatives
        — recognize the class by suffix/stem: -gestrel, -gestone, -estradiol, -estrone,
          -estriol, testo-, -andro-, medroxy-, ethinyl-, mestranol, tibolone
        — Apply this rule even if an abstract reports a survival correlation;
          observational survival ≠ mechanistic cure.
      EXCEPTION: A hormone-pathway ANTAGONIST (e.g., selective estrogen receptor
      degraders/modulators like fulvestrant, tamoxifen; aromatase inhibitors like
      letrozole, anastrozole; antiandrogens like enzalutamide) is NOT excluded —
      these BLOCK the disease-driving signal and are valid candidates.

  (b) SUPPORTIVE-CARE-ONLY AGENTS.
      Reject corticosteroids used purely for symptom management (hydrocortisone,
      prednisolone, prednisone, dexamethasone-as-antiemetic, tixocortol, fludrocortisone),
      antiemetics, analgesics, and laxatives. They are palliative, not disease-modifying.
      EXCEPTION: dexamethasone in multiple myeloma, where it is part of the curative
      regimen — context matters, defer to the abstracts.

  (c) NUTRITIONAL / DIAGNOSTIC AGENTS.
      Reject vitamins, minerals, contrast agents, and radiotracers unless an abstract
      explicitly demonstrates a disease-modifying mechanism for the target indication.

RULE 3 — MECHANISM-DIRECTION VERIFICATION (THE QUOTED-VERB TEST).
For any drug that survives Rules 1–2, you must locate at least ONE direct verb-phrase
in the abstracts that establishes a CURATIVE direction of action. Acceptable verb-phrases include:
   "inhibits", "antagonizes", "blocks", "suppresses", "induces apoptosis in",
   "downregulates", "degrades", "kills [cell line]", "reduces tumor volume",
   "decreases proliferation", "shrinks tumors", "improved progression-free survival",
   "achieved objective response rate of X%"
Disqualifying verb-phrases (these indicate the drug DRIVES the disease):
   "stimulates", "promotes growth of", "upregulates [oncogenic target]",
   "activates [proliferative pathway]", "increased tumor incidence"
You must record the exact source verb-phrase and the abstract index it came from
(e.g., "Abstract 2: 'induces caspase-3-mediated apoptosis'") in your scratchpad.
If you cannot find a curative verb-phrase, the drug FAILS Rule 3.

RULE 4 — EVIDENCE-TIER CLASSIFICATION.
Classify the strongest available evidence for each surviving drug into one tier:
   TIER A — Phase II/III clinical trial data with positive primary endpoint for {disease_english}
   TIER B — Phase I trial OR multiple (>=2) independent preclinical studies (in vivo + in vitro)
   TIER C — Single in vitro / cell-line study with clear mechanistic data
   TIER D — Observational / retrospective / mechanistic-hypothesis-only
You may only RECOMMEND drugs at Tier A, B, or C. Tier D drugs must be listed under
"watch_list" (see output schema) but NOT in the main recommendations array.

RULE 5 — CONSENSUS BONUS, NOT REQUIREMENT.
If 2 or more abstracts independently corroborate the curative mechanism, mark
"consensus": true. A single high-quality Tier A or Tier B paper is sufficient on
its own; do not penalize a drug for having only one strong source.

RULE 6 — THE EMPTY-SET MANDATE (HALLUCINATION GUARDRAIL).
You are evaluating ONLY the drugs in the dossier above. You must NOT:
   - introduce drugs not in the dossier
   - cite evidence not in the abstracts
   - infer trial results, dosages, or mechanisms beyond what is written
   - soften "no evidence" into "promising" to fill the response
If zero drugs survive Rules 1–4, return an empty "recommendations" array. This is
a CORRECT and EXPECTED outcome for hormone-dominated or steroid-dominated candidate
lists. There is no minimum number of recommendations.

RULE 7 — SPECIFICITY PREFERENCE (TIE-BREAKER).
When choosing among multiple qualifying drugs, prefer in this order:
   1. Targeted agents (kinase inhibitors, mAbs, PARP inhibitors, selective receptor
      antagonists, proteasome inhibitors) over broad-spectrum cytotoxics.
   2. Drugs with established safety profiles in the relevant patient population.
   3. Drugs with mechanism most distinct from existing standard-of-care.

═══════════════════════════════════════════════════════════════════════
SECTION 4 — CHAIN-OF-THOUGHT PROTOCOL (MANDATORY)
═══════════════════════════════════════════════════════════════════════
For EVERY drug in the dossier (including ones you reject), populate the
"evaluation_scratchpad" field with a numbered trace:

   [1] Relevance check: did abstracts study {disease_english}? (PASS/FAIL + reason)
   [2] Class check: hormonal agonist? supportive-care? (PASS/FAIL + class identified)
   [3] Direction check: quoted curative verb-phrase + abstract index, OR quoted
       disqualifying verb-phrase + abstract index
   [4] Evidence tier: A / B / C / D + one-sentence justification
   [5] Verdict: RECOMMEND | WATCH | REJECT, and which rule triggered the decision

═══════════════════════════════════════════════════════════════════════
SECTION 5 — OUTPUT SCHEMA
═══════════════════════════════════════════════════════════════════════
Return a single JSON object (NOT an array) with this exact shape. No markdown
fences. No prose before or after. Pure JSON only.

{{
  "disease": "{disease_english}",
  "mesh_id": "{target_mesh}",
  "recommendations": [
    {{
      "compound": "DrugName",
      "evaluation_scratchpad": "[1] Relevance: PASS ... [2] Class: PASS ... [3] Direction: PASS ... [4] Tier B ... [5] Verdict: RECOMMEND.",
      "mechanism": "Brief mechanism of action description.",
      "evidence_tier": "B",
      "consensus": true,
      "supporting_abstract_indices": [1, 2, 3],
      "key_quoted_evidence": "Abstract 1: 'exact quote'; Abstract 3: 'exact quote'.",
      "llm_summary": "Two-sentence plain-language summary for the UI card. Cite which abstracts support the claim."
    }}
  ],
  "watch_list": [
    {{
      "compound": "DrugName",
      "evaluation_scratchpad": "[1] ... [5] Verdict: WATCH.",
      "reason": "Tier D evidence only — single retrospective observational study; mechanism not established."
    }}
  ],
  "rejected": [
    {{
      "compound": "DrugName",
      "evaluation_scratchpad": "[1] ... [5] Verdict: REJECT.",
      "rule_violated": "Rule 2a",
      "reason": "Progestin (-gestrel suffix); excluded as hormone-receptor agonist."
    }}
  ],
  "dossier_quality_note": "Optional: one sentence flagging if the dossier was dominated by hormones, steroids, or low-quality evidence."
}}

Return at most 3 entries in "recommendations". Return ALL evaluated drugs across
the three arrays combined (recommendations + watch_list + rejected) so the
researcher can see your full reasoning trail. Output raw JSON only.
"""

    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(prompt)

    clean_json = response.text.replace('```json', '').replace('```', '').strip()
    parsed = json.loads(clean_json)

    # Defensive parsing: tolerate slightly malformed LLM output shapes
    if isinstance(parsed, list):
        # Old-schema fallback: model returned a bare array
        parsed = {
            "disease": disease_english,
            "mesh_id": target_mesh,
            "recommendations": parsed,
            "watch_list": [],
            "rejected": [],
            "dossier_quality_note": ""
        }

    recommendations = parsed.get("recommendations", []) or []
    watch_list = parsed.get("watch_list", []) or []
    rejected = parsed.get("rejected", []) or []

    # Inject PubMed URLs into every entry for frontend rendering
    def attach_urls(entries: List[dict]):
        for entry in entries:
            drug_name = entry.get("compound", "")
            entry["pubmed_urls"] = url_mapping.get(drug_name, [])

            # Map abstract indices to URLs for clickable citation rendering
            indices = entry.get("supporting_abstract_indices", []) or []
            entry["supporting_abstract_urls"] = []
            evidence_for_drug = drug_evidence.get(drug_name, [])
            for idx in indices:
                match = next((a for a in evidence_for_drug if a["index"] == idx), None)
                if match:
                    entry["supporting_abstract_urls"].append({
                        "index": idx,
                        "pmid": match["pmid"],
                        "url": match["url"]
                    })

            # Remove internal scratchpad from client-facing payload
            entry.pop("evaluation_scratchpad", "")

    attach_urls(recommendations)
    attach_urls(watch_list)
    attach_urls(rejected)

    # Collect drugs the LLM did not classify into any category
    classified_names = {c.get("compound") for c in recommendations + watch_list + rejected}
    other_candidates = []
    for drug in curated_drugs:
        name = drug["compound"]
        if name not in classified_names:
            other_candidates.append({
                "compound": name,
                "pubmed_urls": url_mapping.get(name, [])
            })

    return {
        "disease_name": disease_english,
        "mesh_id": target_mesh,
        "top_candidates": recommendations,
        "watch_list": watch_list,
        "rejected": rejected,
        "other_candidates": other_candidates,
        "dossier_quality_note": parsed.get("dossier_quality_note", "")
    }


@app.post("/api/analyze")
async def analyze_mesh(
    mesh_id: str = Form(...),
    prediction_file: UploadFile = File(...)
):
    try:
        file_bytes = await prediction_file.read()
        drugs = parse_prediction_file(file_bytes, prediction_file.filename)
        result = llm_pubmed_summarize(drugs, mesh_id)
        return {"mesh_id": mesh_id, "result": result}

    except json.JSONDecodeError as e:
        print(f"ERROR: LLM returned malformed JSON: {str(e)}")
        raise HTTPException(status_code=502, detail="Gemini LLM returned malformed JSON. Please try again.")
    except Exception as e:
        error_msg = str(e)
        print(f"ERROR: Pipeline failure: {error_msg}")

        if "429" in error_msg or "quota" in error_msg.lower() or "rate" in error_msg.lower():
            raise HTTPException(
                status_code=429,
                detail="Gemini API rate limit exceeded. The free tier allows limited requests per day. Please wait a few minutes and try again, or switch to a paid API key."
            )
        elif "api" in error_msg.lower() and ("key" in error_msg.lower() or "auth" in error_msg.lower()):
            raise HTTPException(
                status_code=401,
                detail="Gemini API authentication failed. Please check that the API key is valid."
            )

        raise HTTPException(status_code=500, detail=error_msg)
