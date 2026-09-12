"""
AICANDO Conversation Viewer — FastAPI Backend
 
Serves conversation data and workspace files to the frontend.
Supports two data sources:
  1. CCR workspace directories (response.txt + output files on disk)
  2. JSON-based conversation files (legacy/simulator format)
 
Environment variables:
  CCR_WORKSPACES_DIR  — Path to the root CCR workspace directory
  CCR_API_URL         — (Optional) URL of a live CCR agent for interactive chat
"""
 
import os
import json
import glob
import csv
import time
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from message_processor import process_conversation, parse_response_txt
 
app = FastAPI(title="AICANDO Conversation Viewer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────
# DIRECTORY CONFIGURATION
# ─────────────────────────────────────────────────────────

# JSON conversation files
CONVERSATIONS_DIR = os.path.join(os.path.dirname(__file__), "conversations")
os.makedirs(CONVERSATIONS_DIR, exist_ok=True)

# CCR workspace root — each subdirectory contains response.txt + output files
# Fallback to local workspaces folder if parent workspace_example does not exist (enables standalone use)
_parent_ws = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "workspace_example"))
_local_ws = os.path.abspath(os.path.join(os.path.dirname(__file__), "workspaces"))

CCR_WORKSPACES_DIR = os.environ.get(
    "CCR_WORKSPACES_DIR",
    _parent_ws if os.path.isdir(_parent_ws) else _local_ws
)

# Simulator workspace (stores files generated during interactive chat)
WORKSPACE_DIR = os.path.join(os.path.dirname(__file__), "workspaces")
os.makedirs(WORKSPACE_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────
# CCR WORKSPACE LOADING
# ─────────────────────────────────────────────────────────

def _load_conversation_json(filepath: str) -> dict:
    """Load a conversation from a JSON file."""
    with open(filepath, "r") as f:
        return json.load(f)


def _load_ccr_workspace(ws_path: str, ws_id: str) -> dict:
    """
    Load a CCR workspace directory as a conversation object.

    Reads response.txt for the conversation log and optionally
    .aicando/chat_config.json for session metadata.
    """
    response_file = os.path.join(ws_path, "response.txt")
    if not os.path.exists(response_file):
        return None

    with open(response_file, "r") as f:
        raw = f.read()

    messages = parse_response_txt(raw)

    # Load optional session metadata
    config = {}
    config_path = os.path.join(ws_path, ".aicando", "chat_config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)

    # Derive title from the first user message
    title = "CCR Analysis Run"
    for msg in messages:
        if msg["role"] == "user":
            title = msg["content"][:60]
            if len(msg["content"]) > 60:
                title += "..."
            break

    mtime = os.path.getmtime(response_file)
    created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))

    return {
        "id": f"ccr-{ws_id}",
        "title": title,
        "created_at": created,
        "model": config.get("model", "gpt-oss-120b"),
        "chat_id": config.get("chat_id", ws_id),
        "source": "ccr",
        "messages": messages,
    }


def _discover_ccr_workspaces() -> list:
    """
    Discover CCR workspace directories under CCR_WORKSPACES_DIR.
    A workspace is any directory containing a response.txt file.

    Returns list of (workspace_id, workspace_path) tuples.
    """
    workspaces = []

    if not os.path.isdir(CCR_WORKSPACES_DIR):
        return workspaces

    # Check if the root directory itself is a workspace
    if os.path.exists(os.path.join(CCR_WORKSPACES_DIR, "response.txt")):
        ws_id = os.path.basename(CCR_WORKSPACES_DIR)
        workspaces.append((ws_id, CCR_WORKSPACES_DIR))
        return workspaces

    # Scan subdirectories for workspaces
    for name in sorted(os.listdir(CCR_WORKSPACES_DIR)):
        sub = os.path.join(CCR_WORKSPACES_DIR, name)
        if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "response.txt")):
            workspaces.append((name, sub))

    return workspaces


# ─────────────────────────────────────────────────────────
# API: CONVERSATION LISTING & RETRIEVAL
# ─────────────────────────────────────────────────────────

@app.get("/api/conversations")
def list_conversations():
    """Return metadata for all available conversations."""
    convos = []

    # Load JSON-based conversations (skip CCR session files to avoid duplicates)
    for fp in sorted(glob.glob(os.path.join(CONVERSATIONS_DIR, "*.json")), reverse=True):
        try:
            convo_id = os.path.splitext(os.path.basename(fp))[0]
            if convo_id.startswith("ccr-"):
                continue
            data = _load_conversation_json(fp)
            convos.append({
                "id": convo_id,
                "title": data.get("title", "Untitled Conversation"),
                "created_at": data.get("created_at", ""),
                "model": data.get("model", "LLM"),
                "message_count": len(data.get("messages", [])),
            })
        except Exception as e:
            print(f"Warning: Skipping {fp}: {e}")

    # Load CCR workspace conversations
    for ws_id, ws_path in _discover_ccr_workspaces():
        try:
            convo_id = f"ccr-{ws_id}"
            session_fp = os.path.join(CONVERSATIONS_DIR, f"{convo_id}.json")

            # Prefer interactive session file if it exists
            if os.path.exists(session_fp):
                data = _load_conversation_json(session_fp)
                convos.append({
                    "id": convo_id,
                    "title": data.get("title", "CCR Analysis Run"),
                    "created_at": data.get("created_at", ""),
                    "model": data.get("model", "gpt-oss-120b"),
                    "message_count": len(data.get("messages", [])),
                })
            else:
                data = _load_ccr_workspace(ws_path, ws_id)
                if data:
                    convos.append({
                        "id": convo_id,
                        "title": data["title"],
                        "created_at": data["created_at"],
                        "model": data.get("model", "gpt-oss-120b"),
                        "message_count": len(data["messages"]),
                    })
        except Exception as e:
            print(f"Warning: Skipping CCR workspace {ws_path}: {e}")

    return {"conversations": convos}


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    """Return full message history for a conversation."""

    # CCR workspace conversation
    if conversation_id.startswith("ccr-"):
        ws_id = conversation_id[4:]

        # Check for interactive session file first
        session_fp = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.json")
        if os.path.exists(session_fp):
            data = _load_conversation_json(session_fp)
            data["messages"] = process_conversation(data.get("messages", []))
            return data

        # Fall back to reading the workspace directory
        for discovered_id, ws_path in _discover_ccr_workspaces():
            if discovered_id == ws_id:
                data = _load_ccr_workspace(ws_path, ws_id)
                if data:
                    return data
        raise HTTPException(status_code=404, detail="CCR workspace not found.")

    # JSON-based conversation
    filepath = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.json")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Conversation not found.")

    data = _load_conversation_json(filepath)
    data["messages"] = process_conversation(data.get("messages", []))
    return data


# ─────────────────────────────────────────────────────────
# API: INTERACTIVE CHAT
# ─────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    prompt: str


@app.post("/api/conversations/{conversation_id}/message")
def post_message(conversation_id: str, payload: ChatRequest):
    """
    Send a message to a conversation. Routes to the live CCR agent
    if CCR_API_URL is configured, otherwise uses the local simulator.
    """
    filepath = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.json")
    if not os.path.exists(filepath):
        data = {
            "id": conversation_id,
            "title": f"New Run: {payload.prompt[:40]}...",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "messages": []
        }
    else:
        data = _load_conversation_json(filepath)

    # Append user message
    data["messages"].append({"role": "user", "content": payload.prompt})

    clean_id = conversation_id.removeprefix("ccr-")
    target_ws = os.path.abspath(os.path.join(CCR_WORKSPACES_DIR, clean_id))
    os.makedirs(target_ws, exist_ok=True)

    # Route to live CCR agent or local simulator
    ccr_api_url = os.environ.get("CCR_API_URL")

    if ccr_api_url:
        try:
            import requests as http_client
            # Pass prompt, conversation ID, and workspace directory so the agent knows where to write output files
            payload_data = {
                "prompt": payload.prompt,
                "conversation_id": clean_id,
                "workspace_dir": target_ws
            }
            resp = http_client.post(ccr_api_url, json=payload_data, timeout=120)
            if resp.ok:
                try:
                    assistant_content = resp.json().get("content", resp.text)
                except ValueError:
                    assistant_content = resp.text
            else:
                assistant_content = f"Error: CCR agent returned status {resp.status_code}: {resp.text}"
        except Exception as e:
            assistant_content = f"Error: Could not reach CCR agent at {ccr_api_url}: {e}"
    else:
        # Simulator mode — generates mock responses for UI development
        time.sleep(1.5)
        assistant_content = _simulate_response(payload.prompt, conversation_id)

    assistant_msg = {"role": "assistant", "content": assistant_content}
    data["messages"].append(assistant_msg)

    # Persist conversation to disk
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    # Return the processed message — pick parser by format.
    # CCR agents emit <|start|>-delimited logs; legacy/simulator emit XML tags.
    if "<|start|>" in assistant_content:
        processed = parse_response_txt(assistant_content)
        return {"message": processed[-1] if processed else {"role": "assistant", "content": assistant_content}}

    processed = process_conversation([assistant_msg])
    return {"message": processed[0]}


def _simulate_response(prompt: str, convo_id: str) -> str:
    """
    Generate a mock response for UI development and testing.
    Produces realistic CoT + tool call output when drug-related
    keywords are detected; otherwise returns a capabilities overview.
    """
    ws_path = os.path.join(WORKSPACE_DIR, convo_id)
    os.makedirs(ws_path, exist_ok=True)
    p = prompt.lower()

    if any(kw in p for kw in ["breast", "cancer", "mesh", "pubmed", "drug", "compound"]):
        # Generate a sample results file
        tsv_path = os.path.join(ws_path, "search_results.tsv")
        with open(tsv_path, "w") as f:
            f.write("Compound\tPubMed_ID\tPhase\tOutcome\tVerdict\n")
            f.write("Lapatinib\t19283741\tPhase III\tPositive\tRECOMMEND\n")
            f.write("Palbociclib\t28374192\tPhase II\tPromising\tPOTENTIAL\n")
            f.write("Ribociclib\t37419283\tPhase II\tOngoing\tINVESTIGATE\n")

        return (
            "<thinking>\n"
            "The user is asking about cancer drug repurposing.\n"
            "I should query PubMed for relevant clinical trials and cross-reference\n"
            "with CANDO prediction scores. Saving results to search_results.tsv.\n"
            "</thinking>\n"
            "<tool_use>{\"name\": \"search_pubmed\", \"arguments\": "
            "{\"query\": \"" + prompt[:50] + "\", \"max_results\": 5}}</tool_use>\n"
            "<tool_result>Found 3 clinical trials. Details saved to search_results.tsv.</tool_result>\n\n"
            "Based on the CANDO predictions and clinical trial evidence:\n\n"
            "| Compound | PubMed ID | Phase | Outcome | Verdict |\n"
            "|---|---|---|---|---|\n"
            "| **Lapatinib** | [19283741](https://pubmed.ncbi.nlm.nih.gov/19283741) | Phase III | Positive | RECOMMEND |\n"
            "| **Palbociclib** | [28374192](https://pubmed.ncbi.nlm.nih.gov/28374192) | Phase II | Promising | POTENTIAL |\n"
            "| **Ribociclib** | [37419283](https://pubmed.ncbi.nlm.nih.gov/37419283) | Phase II | Ongoing | INVESTIGATE |\n\n"
            "Full results have been saved to `search_results.tsv` in your workspace."
        )
    else:
        return (
            "<thinking>\n"
            "General query -- provide an overview of capabilities.\n"
            "</thinking>\n\n"
            "Hello! I am **AICANDO**, a computational drug discovery assistant.\n\n"
            "I can help you with:\n"
            "- **Drug repurposing analysis** -- Search CANDO predictions for any MeSH disease ID\n"
            "- **Literature review** -- Query PubMed for clinical trial evidence\n"
            "- **Data export** -- Generate TSV/CSV reports in your workspace\n\n"
            "Try asking me something like:\n"
            "> *\"Search PubMed for breast cancer drug clinical trials\"*\n\n"
            "> *\"Analyze CANDO predictions for MeSH: D001943\"*"
        )


# ─────────────────────────────────────────────────────────
# API: WORKSPACE FILE VIEWER
# ─────────────────────────────────────────────────────────

def _get_workspace_path(conversation_id: str) -> str:
    """Resolve the workspace directory path for a conversation."""
    clean_id = conversation_id.removeprefix("ccr-")

    # Check first in CCR_WORKSPACES_DIR (the folder for active runs and loaded logs)
    path_in_ccr = os.path.abspath(os.path.join(CCR_WORKSPACES_DIR, clean_id))
    if os.path.isdir(path_in_ccr):
        return path_in_ccr

    # Search discovered workspaces
    for discovered_id, ws_path in _discover_ccr_workspaces():
        if discovered_id == clean_id:
            return ws_path

    # Fall back to local simulator directory
    ws_path = os.path.abspath(os.path.join(WORKSPACE_DIR, conversation_id))
    if os.path.isdir(ws_path):
        return ws_path
    return None


# Files and directories to exclude from workspace listings
SKIP_FILES = {".DS_Store", "response.txt", "chat_config.json"}
SKIP_DIRS = {".aicando", "__pycache__", ".git", "out"}


@app.get("/api/workspace/{conversation_id}/files")
def list_workspace_files(conversation_id: str):
    """List all output files in the workspace directory (recursive)."""
    ws_path = _get_workspace_path(conversation_id)
    if not ws_path or not os.path.isdir(ws_path):
        return {"files": []}

    files = []
    for root, dirs, filenames in os.walk(ws_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

        for name in sorted(filenames):
            if name in SKIP_FILES or name.startswith("."):
                continue
            fp = os.path.join(root, name)
            rel_path = os.path.relpath(fp, ws_path)
            ext = os.path.splitext(name)[1].lower()
            files.append({
                "name": rel_path,
                "display_name": name,
                "extension": ext,
                "size_bytes": os.path.getsize(fp),
                "type": _classify_file(ext),
            })
    return {"files": files}


@app.get("/api/workspace/{conversation_id}/file/{filepath:path}")
def get_workspace_file(conversation_id: str, filepath: str):
    """
    Return file contents for preview.
    CSV/TSV files are parsed into JSON tables; text files returned as raw content.
    """
    ws_path = _get_workspace_path(conversation_id)
    if not ws_path:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    fp = os.path.join(ws_path, filepath)

    # Prevent path traversal attacks
    if not os.path.abspath(fp).startswith(os.path.abspath(ws_path)):
        raise HTTPException(status_code=403, detail="Path traversal denied.")

    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="File not found.")

    ext = os.path.splitext(filepath)[1].lower()

    # CSV/TSV → structured table
    if ext in ('.csv', '.tsv'):
        delimiter = '\t' if ext == '.tsv' else ','
        with open(fp, 'r') as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            rows = list(reader)
            headers = reader.fieldnames or []
        return {
            "type": "table",
            "headers": headers,
            "rows": rows[:500],
            "total_rows": len(rows),
            "truncated": len(rows) > 500,
        }

    # PDB → text preview (first 200 lines)
    if ext == '.pdb':
        with open(fp, 'r') as f:
            lines = f.readlines()
        preview = "".join(lines[:200])
        return {
            "type": "text",
            "content": preview + (f"\n\n... ({len(lines)} total lines)" if len(lines) > 200 else ""),
            "language": "text",
        }

    # Other text files → raw content (capped at 100KB)
    if ext in ('.txt', '.md', '.json', '.py', '.log', '.html', '.out'):
        with open(fp, 'r') as f:
            content = f.read(100_000)
        return {
            "type": "text",
            "content": content,
            "language": _ext_to_language(ext),
        }

    return {"type": "binary", "message": f"Cannot preview {ext} files in the browser."}


# ─────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────

def _classify_file(ext: str) -> str:
    """Classify a file extension into a display category."""
    if ext in ('.csv', '.tsv'): return 'table'
    if ext in ('.txt', '.md', '.log', '.out'): return 'text'
    if ext in ('.json',): return 'json'
    if ext in ('.py',): return 'code'
    if ext in ('.pdb',): return 'structure'
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.svg'): return 'image'
    return 'other'


def _ext_to_language(ext: str) -> str:
    """Map file extension to syntax highlighting language."""
    return {'.py': 'python', '.json': 'json', '.md': 'markdown',
            '.html': 'html', '.txt': 'text', '.log': 'text', '.out': 'text'}.get(ext, 'text')
