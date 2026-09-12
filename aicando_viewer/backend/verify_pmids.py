#!/usr/bin/env python3
import os, json, re, sys
from collections import defaultdict

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "discovery_outputs", "prompts")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "discovery_outputs", "results")

def normalize_drug_name(name):
    """Aggressive normalization for drug names."""
    # Remove all non-alphanumeric characters (spaces, hyphens, underscores)
    return re.sub(r'[^a-z0-9]', '', name.lower().strip())

def extract_pmids(text):
    """Extract all PMID numbers from a string."""
    return set(re.findall(r'PMID:?\s*(\d+)', text, re.IGNORECASE))

def main():
    prompt_files = sorted([f for f in os.listdir(PROMPTS_DIR) if f.startswith("prompt_") and f.endswith(".txt")])
    
    total_cited_pmids = 0
    valid_dossier_pmids = 0
    hallucinated_pmids = 0
    
    total_suggestions = 0
    exact_overlap = 0
    fuzzy_overlap = 0
    
    for prompt_file in prompt_files:
        prompt_path = os.path.join(PROMPTS_DIR, prompt_file)
        result_path = os.path.join(RESULTS_DIR, prompt_file)
        
        if not os.path.exists(result_path):
            continue
            
        # Parse prompt to get existing drugs and dossier PMIDs
        with open(prompt_path, "r") as f:
            prompt_content = f.read()
            
        dossier_pmids = extract_pmids(prompt_content)
        
        # Extract existing drugs
        existing_drugs = []
        in_drug_list = False
        for line in prompt_content.split('\n'):
            stripped = line.strip()
            if "currently maps the following drugs" in stripped:
                in_drug_list = True
                continue
            if in_drug_list:
                if stripped.startswith("- "):
                    existing_drugs.append(stripped[2:].strip())
                elif stripped == "" and len(existing_drugs) > 0:
                    continue
                elif not stripped.startswith("- ") and stripped != "" and len(existing_drugs) > 0:
                    break
                    
        existing_norm = [normalize_drug_name(d) for d in existing_drugs]
        
        # Parse result
        with open(result_path, "r") as f:
            result_content = f.read()
            
        # 1. Check PMIDs in result
        json_match = re.search(r'\{[\s\S]*"discoveries"[\s\S]*\}', result_content)
        if json_match:
            try:
                data = json.loads(json_match.group())
                for disc in data.get("discoveries", []):
                    total_suggestions += 1
                    sugg_name = disc.get("drug_name", "").strip()
                    sugg_norm = normalize_drug_name(sugg_name)
                    
                    # Overlap Check
                    if sugg_norm in existing_norm:
                        exact_overlap += 1
                        fuzzy_overlap += 1
                    else:
                        # Fuzzy substring check
                        is_fuzzy = False
                        for ex in existing_norm:
                            if ex in sugg_norm or sugg_norm in ex:
                                is_fuzzy = True
                                break
                        if is_fuzzy:
                            fuzzy_overlap += 1
                            
                    # PMID Check
                    evidence = disc.get("key_evidence", "")
                    cited_pmids = extract_pmids(evidence)
                    for pmid in cited_pmids:
                        total_cited_pmids += 1
                        if pmid in dossier_pmids:
                            valid_dossier_pmids += 1
                        else:
                            hallucinated_pmids += 1
            except json.JSONDecodeError:
                pass

    print("=" * 60)
    print("  RIGOROUS VERIFICATION ANALYSIS")
    print("=" * 60)
    print(f"Total Drug Suggestions Evaluated: {total_suggestions}")
    
    print("\n--- 1. OVERLAP ANALYSIS (Strict vs Fuzzy) ---")
    if total_suggestions > 0:
        print(f"Exact Normalized Overlap: {exact_overlap} ({(exact_overlap/total_suggestions)*100:.2f}%)")
        print(f"Fuzzy Substring Overlap:  {fuzzy_overlap} ({(fuzzy_overlap/total_suggestions)*100:.2f}%)")
        print(f"True Novelty Rate (Fuzzy): {total_suggestions - fuzzy_overlap} ({((total_suggestions - fuzzy_overlap)/total_suggestions)*100:.2f}%)")
    
    print("\n--- 2. PMID HALLUCINATION CHECK ---")
    print(f"Total PMIDs Cited in JSON output: {total_cited_pmids}")
    if total_cited_pmids > 0:
        print(f"Valid PMIDs (Found in Dossier):   {valid_dossier_pmids} ({(valid_dossier_pmids/total_cited_pmids)*100:.2f}%)")
        print(f"Hallucinated/External PMIDs:      {hallucinated_pmids} ({(hallucinated_pmids/total_cited_pmids)*100:.2f}%)")
    
if __name__ == "__main__":
    main()
