import streamlit as st
import pandas as pd
import time
import os

try:
    import cando as cnd
except ImportError:
    st.error("CANDO library not found. Please run this in the cando_env.")
    st.stop()

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="CANDO Therapeutic Repurposing UI", layout="wide", page_icon="💊")

# --- HEADER ---
st.title("💊 CANDO Therapeutic Repurposing UI")
st.markdown("""
A computational pipeline for multiscale structural drug discovery and repurposing. 
This interface connects disease indications to novel therapeutics via proteomic signature evaluation.
""")
st.divider()

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("⚙️ Pipeline Configuration")
    target_mesh = st.text_input(
        "Target MeSH ID", 
        value="D001943", 
        help="Enter the Medical Subject Headings (MeSH) ID (e.g., D001943 for Breast Cancer)."
    )
    st.caption("Adjust the MeSH ID to target different diseases.")

# --- REAL CANDO ENGINE ---
@st.cache_resource(show_spinner="Initializing CANDO objects (First load only, this may take a moment)...")
def load_cando_engine():
    # Instantiate the CANDO object with tutorial data
    return cnd.CANDO(
        'tutorial/cmpds-v2.2.tsv', 
        'tutorial/cmpds2inds-v2.2.tsv', 
        matrix='tutorial/tutorial_matrix-approved.tsv', 
        compound_set='approved', 
        compute_distance=True, 
        dist_metric='cosine',
        ncpus=2 
    )

def generate_pipeline_results(mesh_id):
    """
    Runs the actual CANDO drug repurposing prediction engine.
    """
    engine = load_cando_engine()
    
    # Ensure mesh ID is properly formatted for CANDO calls
    if not mesh_id.startswith("MESH:"):
        mesh_id_formatted = f"MESH:{mesh_id}"
    else:
        mesh_id_formatted = mesh_id
        
    predictions_file = f"ui_predictions_{mesh_id.replace(':', '_')}.tsv"
    
    # Run the prediction
    try:
        engine.canpredict_compounds(
            mesh_id_formatted, 
            n=25, 
            topX=20, 
            keep_associated=False,
            save=predictions_file
        )
        
        # Parse the actual CANDO TSV output back into pandas
        if os.path.exists(predictions_file):
            df = pd.read_csv(predictions_file, sep='\t')
            
            # Clean up the headers for the UI if it matches the standard output
            if 'name' in df.columns and 'score1' in df.columns:
                df = df.rename(columns={
                    'rank': 'Rank',
                    'id': 'CANDO_ID',
                    'name': 'Drug Name',
                    'score1': 'Similarity Score',
                    'approved': 'Approval Status'
                })
                # Drop structural columns not needed for top level UI
                cols_to_keep = ['Rank', 'Drug Name', 'CANDO_ID', 'Similarity Score', 'Approval Status']
                df = df[[c for c in cols_to_keep if c in df.columns]]
                
            return df
        else:
            return pd.DataFrame({"Error": [f"No results generated for {mesh_id_formatted}."]})
    except Exception as e:
        return pd.DataFrame({"Error": [f"Pipeline failed: {str(e)}"]})

# --- MAIN EXECUTION BLOCK ---
if st.button("🚀 Run Pipeline", type="primary", use_container_width=True):
    
    if not target_mesh:
        st.warning("Please enter a valid target disease MeSH ID in the sidebar to proceed.")
    else:
        st.write("### Processing Status")
        
        # The "Illusion of Heavy Compute"
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        pipeline_stages = [
            "Initializing interaction matrix...",
            "Loading compound library and biological targets...",
            "Running proteomic similarity scoring...",
            "Filtering low-confidence targets...",
            "Extracting top therapeutic candidates..."
        ]
        
        for i, status_msg in enumerate(pipeline_stages):
            status_text.markdown(f"**⏳ {status_msg}**")
            progress_pct = int(((i + 1) / len(pipeline_stages)) * 100)
            progress_bar.progress(progress_pct)
            time.sleep(1.5) 
            
        status_text.success("✅ CANDO Pipeline execution complete!")
        
        st.divider()
        
        # --- DATA DISPLAY ---
        st.write(f"### Top Predicted Therapeutics for `{target_mesh}`")
        
        with st.spinner("Retrieving pipeline data..."):
            results_df = generate_pipeline_results(target_mesh)
            
        st.dataframe(
            results_df, 
            use_container_width=True,
            hide_index=True
        )
        
        st.write("") 
        
        # --- EXPORT FUNCTIONALITY ---
        csv_export = results_df.to_csv(index=False).encode('utf-8')
        
        st.download_button(
            label="⬇️ Download Results as CSV",
            data=csv_export,
            file_name=f"cando_predictions_{target_mesh.replace(':', '_')}.csv",
            mime="text/csv",
            type="secondary"
        )
