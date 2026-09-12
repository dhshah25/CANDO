#!/usr/bin/env python3
"""
NOVELTY ANALYSIS — READ-ONLY
Cross-checks LLM drug suggestions against existing CANDO mappings.
Does NOT modify or delete any files.

Outputs:
  - Per-disease novelty counts
  - Overall novelty rate
  - Summary statistics
"""

import os, json, re, sys
from collections import defaultdict

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "discovery_outputs", "prompts")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "discovery_outputs", "results")
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "discovery_reports")

def extract_existing_drugs_from_prompt(prompt_path):
    """Parse the prompt file to extract the list of drugs already in CANDO."""
    existing = set()
    with open(prompt_path, "r") as f:
        lines = f.readlines()
    
    in_drug_list = False
    for line in lines:
        stripped = line.strip()
        # The existing drugs section starts after "currently maps the following drugs"
        if "currently maps the following drugs" in stripped:
            in_drug_list = True
            continue
        if in_drug_list:
            if stripped.startswith("- "):
                drug = stripped[2:].strip().lower()
                existing.add(drug)
            elif stripped == "" and len(existing) > 0:
                # Empty line after drug list = end of section
                # But allow blank lines between drugs
                continue
            elif not stripped.startswith("- ") and stripped != "" and len(existing) > 0:
                # Non-drug line after we started collecting = end
                break
    return existing


def extract_suggested_drugs_from_result(result_path):
    """Parse the LLM result .txt file to extract suggested drug names."""
    suggested = []
    with open(result_path, "r") as f:
        content = f.read()
    
    # Try to find JSON block in the content
    # The result files may contain raw JSON or JSON embedded in text
    json_match = re.search(r'\{[\s\S]*"discoveries"[\s\S]*\}', content)
    if json_match:
        try:
            data = json.loads(json_match.group())
            for disc in data.get("discoveries", []):
                name = disc.get("drug_name", "").strip().lower()
                display = disc.get("drug_name_display", "").strip().lower()
                if name:
                    suggested.append({
                        "name": name,
                        "display": display or name,
                        "evidence_level": disc.get("evidence_level", "unknown"),
                        "key_evidence": disc.get("key_evidence", ""),
                    })
            return suggested
        except json.JSONDecodeError:
            pass
    
    # Fallback: try to find drug_name fields with regex
    for m in re.finditer(r'"drug_name"\s*:\s*"([^"]+)"', content):
        suggested.append({
            "name": m.group(1).strip().lower(),
            "display": m.group(1),
            "evidence_level": "unknown",
            "key_evidence": "",
        })
    
    return suggested


def normalize_drug_name(name):
    """Normalize drug name for comparison (lowercase, remove underscores/hyphens)."""
    return re.sub(r'[_\-\s]+', '', name.lower().strip())


def main():
    print("=" * 70)
    print("  NOVELTY ANALYSIS — READ-ONLY (No files modified)")
    print("=" * 70)
    
    # Collect all prompt files
    prompt_files = sorted([f for f in os.listdir(PROMPTS_DIR) if f.startswith("prompt_") and f.endswith(".txt")])
    print(f"\nFound {len(prompt_files)} prompt files in prompts/")
    
    # Collect all result files (the .txt ones, not .analysis.txt)
    result_txt_files = set()
    if os.path.exists(RESULTS_DIR):
        for f in os.listdir(RESULTS_DIR):
            if f.endswith(".txt") and not f.endswith(".analysis.txt"):
                result_txt_files.add(f)
    print(f"Found {len(result_txt_files)} result .txt files in results/")
    
    # Also check discovery_reports/ JSON files
    report_files = set()
    if os.path.exists(REPORTS_DIR):
        for f in os.listdir(REPORTS_DIR):
            if f.endswith("_discovery.json"):
                report_files.add(f)
    print(f"Found {len(report_files)} discovery report .json files in discovery_reports/")
    
    # Stats
    total_diseases_with_results = 0
    total_suggestions = 0
    total_novel = 0
    total_overlap = 0
    total_diseases_no_existing = 0
    diseases_with_100pct_novel = 0
    diseases_with_0pct_novel = 0
    biologics_detected = 0
    
    has_pmid_count = 0
    clinical_consensus_count = 0
    
    novel_drugs_global = defaultdict(int)  # drug_name -> count of diseases it appears as novel
    overlap_drugs_global = defaultdict(int)
    
    disease_results = []
    
    for prompt_file in prompt_files:
        mesh_id = prompt_file.replace("prompt_", "").replace(".txt", "")
        
        # Extract existing drugs from prompt
        prompt_path = os.path.join(PROMPTS_DIR, prompt_file)
        existing_drugs = extract_existing_drugs_from_prompt(prompt_path)
        existing_normalized = {normalize_drug_name(d) for d in existing_drugs}
        
        # Find matching result file
        result_file = prompt_file  # same name in results/
        result_path = os.path.join(RESULTS_DIR, result_file)
        
        if not os.path.exists(result_path):
            continue
        
        # Extract suggested drugs from result
        suggested = extract_suggested_drugs_from_result(result_path)
        
        if not suggested:
            continue
        
        total_diseases_with_results += 1
        
        if len(existing_drugs) == 0:
            total_diseases_no_existing += 1
        
        novel_count = 0
        overlap_count = 0
        
        for drug in suggested:
            total_suggestions += 1
            drug_norm = normalize_drug_name(drug["name"])
            
            # Check for biologics that slipped through
            if any(drug["name"].endswith(suffix) for suffix in ["mab", "umab", "zumab", "ximab", "tinib"]):
                # tinib is actually a small molecule (kinase inhibitor), so don't flag it
                if drug["name"].endswith("mab") or drug["name"].endswith("umab") or drug["name"].endswith("zumab") or drug["name"].endswith("ximab"):
                    biologics_detected += 1
            
            # Check PMID vs Clinical consensus
            evidence = drug.get("key_evidence", "")
            if "PMID" in evidence or "pmid" in evidence:
                has_pmid_count += 1
            elif "consensus" in evidence.lower() or "clinical consensus" in evidence.lower():
                clinical_consensus_count += 1
            
            # Novelty check
            is_overlap = drug_norm in existing_normalized
            if is_overlap:
                overlap_count += 1
                total_overlap += 1
                overlap_drugs_global[drug["name"]] += 1
            else:
                novel_count += 1
                total_novel += 1
                novel_drugs_global[drug["name"]] += 1
        
        novelty_pct = (novel_count / len(suggested) * 100) if suggested else 0
        
        if novelty_pct == 100:
            diseases_with_100pct_novel += 1
        elif novelty_pct == 0:
            diseases_with_0pct_novel += 1
        
        disease_results.append({
            "mesh_id": mesh_id,
            "existing_count": len(existing_drugs),
            "suggested_count": len(suggested),
            "novel_count": novel_count,
            "overlap_count": overlap_count,
            "novelty_pct": novelty_pct,
        })
    
    # Print Summary
    print("\n" + "=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    
    print(f"\n  Diseases with LLM results:     {total_diseases_with_results}")
    print(f"  Diseases with no existing CANDO drugs: {total_diseases_no_existing}")
    print(f"\n  Total Drug Suggestions:        {total_suggestions}")
    print(f"  ├── Novel (NOT in CANDO):      {total_novel}  ({total_novel/max(total_suggestions,1)*100:.1f}%)")
    print(f"  └── Overlap (already in CANDO): {total_overlap}  ({total_overlap/max(total_suggestions,1)*100:.1f}%)")
    
    print(f"\n  Diseases with 100% novel drugs: {diseases_with_100pct_novel}")
    print(f"  Diseases with 0% novel drugs:   {diseases_with_0pct_novel}")
    
    print(f"\n  --- Evidence Quality ---")
    print(f"  Suggestions with PMID cited:    {has_pmid_count}  ({has_pmid_count/max(total_suggestions,1)*100:.1f}%)")
    print(f"  Suggestions with 'Clinical consensus': {clinical_consensus_count}  ({clinical_consensus_count/max(total_suggestions,1)*100:.1f}%)")
    
    print(f"\n  --- Biologics Leak Check ---")
    print(f"  Biologics (-mab) detected:      {biologics_detected}  ({biologics_detected/max(total_suggestions,1)*100:.1f}%)")
    
    # Top 20 most frequently suggested novel drugs
    print(f"\n  --- Top 20 Most Frequently Suggested NOVEL Drugs ---")
    top_novel = sorted(novel_drugs_global.items(), key=lambda x: x[1], reverse=True)[:20]
    for i, (drug, count) in enumerate(top_novel, 1):
        print(f"  {i:2d}. {drug:<35s} (suggested for {count} diseases)")
    
    # Top 10 most frequently overlapping drugs
    if overlap_drugs_global:
        print(f"\n  --- Top 10 Most Frequently OVERLAPPING Drugs ---")
        top_overlap = sorted(overlap_drugs_global.items(), key=lambda x: x[1], reverse=True)[:10]
        for i, (drug, count) in enumerate(top_overlap, 1):
            print(f"  {i:2d}. {drug:<35s} (overlaps in {count} diseases)")
    
    # Distribution of novelty percentages
    print(f"\n  --- Novelty Distribution ---")
    buckets = {"0%": 0, "1-25%": 0, "26-50%": 0, "51-75%": 0, "76-99%": 0, "100%": 0}
    for dr in disease_results:
        p = dr["novelty_pct"]
        if p == 0: buckets["0%"] += 1
        elif p <= 25: buckets["1-25%"] += 1
        elif p <= 50: buckets["26-50%"] += 1
        elif p <= 75: buckets["51-75%"] += 1
        elif p < 100: buckets["76-99%"] += 1
        else: buckets["100%"] += 1
    
    for bucket, count in buckets.items():
        bar = "█" * (count // 5)
        print(f"  {bucket:>8s}: {count:4d} diseases {bar}")
    
    print("\n" + "=" * 70)
    print("  ANALYSIS COMPLETE — No files were modified or deleted.")
    print("=" * 70)


if __name__ == "__main__":
    main()
