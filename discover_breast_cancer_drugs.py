import os
import sys

def main():
    try:
        import cando as cnd
    except ImportError:
        print("Error: The 'cando' library is not installed in the current environment.")
        print("Please ensure you are running this script within the 'cando_env' environment.")
        sys.exit(1)

    print("--- Starting CANDO Breast Cancer Drug Discovery ---")
    
    # 1. Define file paths for the tutorial data
    cmpd_map = 'tutorial/cmpds-v2.2.tsv'
    ind_map = 'tutorial/cmpds2inds-v2.2.tsv'
    matrix_file = 'tutorial/tutorial_matrix-approved.tsv'
    
    # Verify files exist before proceeding
    for f in [cmpd_map, ind_map, matrix_file]:
        if not os.path.exists(f):
            print(f"Error: Missing required data file: {f}")
            print("Please ensure you are running this script from the 'tutorial' directory")
            print("where the CANDO tutorial data is located.")
            sys.exit(1)

    print("1. Loading CANDO Object with tutorial data (approved compounds only)...")
    # 2. Instantiate the CANDO object
    # We use 'approved' to focus on repurposing existing safe drugs
    cando = cnd.CANDO(
        cmpd_map, 
        ind_map, 
        matrix=matrix_file, 
        compound_set='approved', 
        compute_distance=True, 
        dist_metric='cosine', # using cosine as per tutorial
        ncpus=2 # utilize a couple cores to speed up distance computation
    )

    print(f"\nLoaded {len(cando.compounds)} compounds, {len(cando.indications)} indications, and {len(cando.proteins)} proteins.")

    # 3. Identify the Indication (Breast Cancer)
    # The MeSH ID for "Breast Neoplasms" is MESH:D001943
    breast_cancer_mesh_id = "MESH:D001943"
    try:
        breast_cancer = cando.get_indication(breast_cancer_mesh_id)
        print(f"\n2. Found Indication: {breast_cancer.name} ({breast_cancer.id_})")
        
        # Look at current known treatments
        known_drugs = [cando.get_compound(cmpd).name for cmpd in breast_cancer.compounds]
        print(f"   Currently associated with {len(breast_cancer.compounds)} approved drugs.")
        print(f"   Examples: {', '.join(known_drugs[:5])}...")
        
    except Exception as e:
        print(f"Error locating indication {breast_cancer_mesh_id}: {e}")
        sys.exit(1)

    print("\n3. Generating Novel Treatment Predictions...")
    print("   Comparing the proteomic signatures of known breast cancer drugs against all others...")
    # 4. Generate Predictions
    # We'll look at the top 25 similar compounds for each known drug, 
    # and return the top 20 consensus predictions.
    # keep_associated=False means we ONLY want to see *new* potential treatments, not existing ones.
    predictions_file = "breast_cancer_predictions.tsv"
    
    # Run the prediction and explicitly save to file
    cando.canpredict_compounds(
        breast_cancer_mesh_id, 
        n=25, 
        topX=20, 
        keep_associated=False,
        save=predictions_file
    )
    
    print(f"   Predictions generated and saved to: {predictions_file}")

    print("\n4. Benchmarking the CANDO platform specifically on Breast Cancer...")
    print("\n   Note: The Benchmarking section is commented out to avoid a known PyArrow/ConnectorX multiprocessing bug on Apple Silicon (M2).")
    # This checks how well CANDO "recalls" the known breast cancer drugs 
    # using a leave-one-out cross-validation approach.
    # benchmark_name = "breast_cancer_benchmark"
    # cando.canbenchmark_new(
    #     benchmark_name, 
    #     n=25, 
    #     indications=[breast_cancer_mesh_id]
    # )
    
    # print(f"   Benchmarking complete. Summary saved to: summary-{benchmark_name}-25-associated.tsv")
    print("\n--- Pipeline Complete ---")
    print("Please review the 'breast_cancer_predictions.tsv' file for your new drug candidates!")

if __name__ == "__main__":
    main()
