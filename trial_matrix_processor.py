import pandas as pd
import numpy as np

def process_interaction_matrix(file_path: str, output_path: str, target_proteins: list) -> pd.DataFrame:
    """
    Automated Ingestion and Matrix Transformation:
    Loads a dense interaction matrix (TSV), melts it into a relational format,
    calculates the 99th percentile of interaction scores, filters the top 1%,
    cross-references against a target biomarker list, and exports the results.

    Arguments:
        file_path (str): The path to the input TSV interaction matrix.
        output_path (str): The path where the final CSV should be saved.
        target_proteins (list): A list of target Protein IDs to filter against.
        
    Returns:
        pd.DataFrame: The final sorted dataframe containing top 1% hits for target proteins.
    """
    
    # We use a try-except block to gracefully handle file not found errors
    # during this trial script execution if 'cando_matrix.tsv' doesn't exist yet.
    try:
        # Load the wide matrix. Assuming the first column is Drug_IDs
        df_wide = pd.read_csv(file_path, sep='\t', index_col=0)
        print(f"Original matrix shape: {df_wide.shape}")
    except FileNotFoundError:
        print(f"File '{file_path}' not found. Generating a mock dense matrix for demonstration...")
        # Mock data generation if file is missing (to ensure script runs on your machine)
        np.random.seed(42)
        mock_drugs = [f'Drug_{i}' for i in range(1, 1001)]
        mock_proteins = ['BRCA1', 'BRCA2', 'ERBB2', 'ESR1', 'PGR'] + [f'Protein_{i}' for i in range(6, 1001)]
        mock_data = np.random.normal(loc=0.5, scale=0.2, size=(1000, 1000))
        df_wide = pd.DataFrame(mock_data, index=mock_drugs, columns=mock_proteins)
        print(f"Mock Original matrix shape: {df_wide.shape}")

    # ==========================================
    # 1. Matrix Transformation
    # ==========================================
    # CANDO tutorial matrix is natively transposed: Rows are Proteins, Columns are Drugs.
    # We transpose it (.T) so Rows are Drugs, Columns are Proteins to match scalable relational logic.
    df_wide = df_wide.T
    
    # Reset index to make the Drug_IDs a standard column before melting
    df_wide = df_wide.reset_index().rename(columns={'index': 'Drug_ID'})
    
    # Melt the incredibly wide matrix into a long 3-column relational format
    # This vectorizes the transformation entirely in C, maximizing Apple Silicon efficiency
    df_long = df_wide.melt(
        id_vars=['Drug_ID'], 
        var_name='Protein_ID', 
        value_name='Interaction_Score'
    )
    print(f"Flattened shape: {df_long.shape}")

    # ==========================================
    # 2. Dynamic Statistical Filtering (Top 1%)
    # ==========================================
    # Calculate the exact 99th percentile across all interaction scores globally.
    # We use the built-in Series quantile function which utilizes efficient C implementations.
    percentile_99 = df_long['Interaction_Score'].quantile(0.99)
    print(f"99th Percentile Threshold value: {percentile_99:.5f}")

    # Filter out anything below this extremely high-affinity threshold using vectorized boolean indexing
    df_top_1_percent = df_long[df_long['Interaction_Score'] >= percentile_99]
    print(f"Shape after 99th percentile filter: {df_top_1_percent.shape}")

    # ==========================================
    # 3. Biomarker Cross-Referencing
    # ==========================================
    # Cross-reference the remaining high-affinity hits against our target Breast Neoplasm markers.
    # .isin() acts as a highly optimized hash map lookup.
    df_breast_cancer_hits = df_top_1_percent[df_top_1_percent['Protein_ID'].isin(target_proteins)]
    print(f"Shape after Biomarker Cross-Referencing: {df_breast_cancer_hits.shape}")

    # ==========================================
    # 4. Export & Final Polish
    # ==========================================
    # Sort the final hits so the highest interaction score is firmly at the top (index 0)
    df_final = df_breast_cancer_hits.sort_values(by='Interaction_Score', ascending=False)
    
    # Save to disk without the pandas numeric index row identifiers
    df_final.to_csv(output_path, index=False)
    print(f"Successfully exported final {df_final.shape[0]} high-affinity hits to: {output_path}")

    return df_final


if __name__ == "__main__":
    # Define our inputs and outputs
    INPUT_FILE = 'tutorial/tutorial_matrix-approved.tsv'
    OUTPUT_FILE = 'top_1_percent_breast_cancer_hits.csv'
    
    # Real list of 5 target protein PDB IDs from the tutorial matrix
    TARGET_MARKERS = ['2xpwA', '3rf1A', '1f92A', '4i7sA', '2hguA']
    
    # Run the pipeline
    final_hits = process_interaction_matrix(
        file_path=INPUT_FILE, 
        output_path=OUTPUT_FILE, 
        target_proteins=TARGET_MARKERS
    )
    
    print("\n--- Top 5 Hits Preview ---")
    print(final_hits.head())
