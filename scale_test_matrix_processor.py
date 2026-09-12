import pandas as pd
import numpy as np
import time

def run_scale_test():
    """
    Simulates the exact conditions claimed on the resume:
    10,000+ FDA-approved compounds x 8,000 proteins = 80,000,000 data points.
    Isolates the top 1% using vectorized pandas operations.
    """
    print("==================================================")
    print("🚀 INIT: 80-Million Data Point Matrix Scale Test 🚀")
    print("==================================================\n")
    
    # ---------------------------------------------------------
    # 1. Data Generation (Simulating the 10GB TSV load into RAM)
    # ---------------------------------------------------------
    num_drugs = 10000
    num_proteins = 8000
    total_data_points = num_drugs * num_proteins
    
    print(f"[{time.strftime('%H:%M:%S')}] Generating synthetic dense matrix in RAM...")
    print(f" -> {num_drugs:,} FDA-Approved Compounds")
    print(f" -> {num_proteins:,} Target Proteins")
    print(f" -> Total Data Points: {total_data_points:,}")
    
    start_gen = time.time()
    # Random normal distribution of interaction scores (mean 0.5, std 0.2)
    # We use float32 to be memory efficient (Apple M2 handles this beautifully)
    np.random.seed(42)
    mock_data = np.random.normal(loc=0.5, scale=0.2, size=(num_drugs, num_proteins)).astype(np.float32)
    
    mock_drugs = [f'Drug_{i}' for i in range(1, num_drugs + 1)]
    # Ensure our Breast Cancer targets exist somewhere in the 8,000
    mock_proteins = ['BRCA1', 'BRCA2', 'ERBB2', 'ESR1', 'PGR'] + [f'Protein_{i}' for i in range(6, num_proteins + 1)]
    
    df_wide = pd.DataFrame(mock_data, index=mock_drugs, columns=mock_proteins)
    elapsed_gen = time.time() - start_gen
    print(f"[{time.strftime('%H:%M:%S')}] ✅ Matrix Generated in {elapsed_gen:.2f} seconds.")
    print(f" -> Memory Usage: {df_wide.memory_usage(deep=True).sum() / 1e9:.2f} GB\n")

    # ---------------------------------------------------------
    # 2. Pipeline Execution (The Resume Claim)
    # ---------------------------------------------------------
    print(f"[{time.strftime('%H:%M:%S')}] Starting Data Engineering Pipeline...")
    start_pipeline = time.time()
    
    # Step A: Flattening the 80 Million Points
    step_start = time.time()
    df_wide = df_wide.reset_index().rename(columns={'index': 'Drug_ID'})
    df_long = df_wide.melt(id_vars=['Drug_ID'], var_name='Protein_ID', value_name='Interaction_Score')
    print(f" -> Melt/Flatten ({total_data_points:,} rows) took: {time.time() - step_start:.2f}s")
    
    # Step B: 99th Percentile Calculation (Top 1%)
    step_start = time.time()
    percentile_99 = df_long['Interaction_Score'].quantile(0.99)
    print(f" -> Calculate 99th Percentile Threshold ({percentile_99:.4f}) took: {time.time() - step_start:.2f}s")
    
    # Step C: Filtering logic
    step_start = time.time()
    df_top_1_percent = df_long[df_long['Interaction_Score'] >= percentile_99]
    print(f" -> Isolate Top 1% Affinity ({len(df_top_1_percent):,} hits) took: {time.time() - step_start:.2f}s")
    
    # Step D: Biomarker Cross-Reference
    step_start = time.time()
    target_proteins = ['BRCA1', 'BRCA2', 'ERBB2', 'ESR1', 'PGR']
    df_final = df_top_1_percent[df_top_1_percent['Protein_ID'].isin(target_proteins)]
    df_final = df_final.sort_values(by='Interaction_Score', ascending=False)
    print(f" -> Correlate with Breast Neoplasm Map ({len(df_final):,} explicit hits) took: {time.time() - step_start:.2f}s")
    
    elapsed_pipeline = time.time() - start_pipeline
    print(f"[{time.strftime('%H:%M:%S')}] ✅ Pipeline Complete in {elapsed_pipeline:.2f} seconds.\n")
    
    print("==================================================")
    print("🏆 FINAL RESULTS PREVIEW 🏆")
    print(f"From {total_data_points:,} interactions to {len(df_final)} highly-specific targets.")
    print("==================================================")
    print(df_final.head())


if __name__ == "__main__":
    run_scale_test()
