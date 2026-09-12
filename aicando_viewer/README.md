# AICANDO Conversational Interface & Workspace Viewer
 
This is a live chat interface and computational workspace viewer. It enables researchers to have a proper, interactive conversation with local drug-discovery agents (e.g. running on the CCR VM), while providing a built-in spreadsheet viewer to inspect output files (like CSVs, TSVs, and PDB files) in real time.
 
---
 
## ⚡ Quick Start (Local Run / Simulator Mode)
 
To run the interface locally on your machine (using the built-in simulator for mock tool executions and file outputs), execute these commands:
 
### 1. Install & Start Backend (FastAPI)
Open a terminal in the project folder and run:
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --port 8001 --reload
```
The backend API will run at `http://localhost:8001`.
 
### 2. Install & Start Frontend (React + Vite)
Open a second terminal in the project folder and run:
```bash
cd frontend
npm install
npm run dev
```
Open your browser to `http://localhost:5173`. You can type prompts in the input bar to test the interactive chat loop and workspace views.
 
---
 
## 🔧 CCR VM Integration (Live Chat)
 
To connect this interface directly to a live agent server running on the CCR VM (so prompts route to the model and output files are synced), refer to the step-by-step instructions in the [Integration Guide](./INTEGRATION_GUIDE.md).
