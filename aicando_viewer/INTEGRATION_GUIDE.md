# AICANDO Conversation Viewer — Integration Guide
 
This guide explains how the viewer is built, how a request flows through it, and the steps to connect the interface with the active LLM agent running on the CCR VM. It reflects the current state of the code — every payload key, directory, and routing rule below is what `main.py` actually does.
 
The viewer has two halves that talk over a small HTTP API. The backend (`backend/`) is a FastAPI server that discovers conversations, parses raw logs into structured messages, and serves workspace output files. The frontend (`frontend/`) is a React + Vite single-page app that lists conversations, renders the chat feed with collapsible reasoning and tool badges, and previews output files. The frontend reaches the backend at `http://localhost:8001` by default, which becomes the host address once deployed.
 
## File responsibilities
 
This table maps the main files of the viewer and their purpose in the project:
 
| File / Directory | Purpose |
|------|-----|
| `backend/main.py` | Exposes the FastAPI endpoints, reads the `CCR_API_URL` environment variable to forward messages, and handles workspace file discovery. |
| `backend/message_processor.py` | Parses conversation logs. Extracts internal chain-of-thought, matches tool calls, and extracts file paths. |
| `frontend/src/App.jsx` | Renders the React chat dashboard, sidebar list, files panel, and performs requests to the backend. |
| `frontend/src/index.css` | Handles layout styling, responsive grids, and scrollable containers. |
| `backend/conversations/` | Automatically created folder storing session JSON metadata. |
| `backend/workspaces/` | Automatically created folder storing the mock output files in simulator mode. |
 
When editing `App.jsx` to update the backend address, search for the string `const API_BASE` (rather than using specific line numbers).
 
## Directory model
 
The backend uses two distinct directories:
 
`CCR_WORKSPACES_DIR` is the root the backend both writes new run folders into and reads existing CCR runs from. When a live agent runs, the backend creates a per-run subdirectory here and passes its absolute path to the agent, and the file viewer later reads output files back from that same subdirectory. This is the directory the CCR scripts and the viewer share. If the environment variable is unset, the backend falls back to a sibling `workspace_example` directory, or, if that does not exist, to the backend's own local `workspaces/` folder so it can run standalone.
 
`WORKSPACE_DIR` (always `backend/workspaces/`) is used only by the local simulator for mock runs during UI development. You do not need to touch it for a live integration.
 
## Request lifecycle
 
When a user sends a prompt in the UI, the following happens in order:
1. The frontend POSTs the prompt to `/api/conversations/{id}/message`.
2. The backend appends the user message, derives a clean run id by stripping any `ccr-` prefix, and creates the run's workspace directory under `CCR_WORKSPACES_DIR`.
3. It then checks whether `CCR_API_URL` is configured. If it is, the backend POSTs to the CCR agent route and uses the reply; if not, it falls back to the local simulator.
4. The assistant reply is appended, the conversation is saved to disk, and the reply is parsed into a structured message and returned.
5. The frontend renders the response, tucks reasoning behind a collapsible panel, turns tool calls into badges, and refreshes the workspace panel so the user can click and preview any files the agent just wrote.
 
## The API contract
 
When `CCR_API_URL` is configured, the backend sends a POST to the VM with this exact JSON body:
 
```json
{
  "prompt": "User query text",
  "conversation_id": "run-id-string",
  "workspace_dir": "/abs/path/to/CCR_WORKSPACES_DIR/run-id-string"
}
```
 
The CCR agent route handles this incoming POST by executing two tasks:
1. **Writes output files**: It saves all output files — CSV, TSV, PDB, and any other artifacts the user should be able to preview — directly into the absolute path given in `workspace_dir`. That directory is automatically created by the backend before the POST is sent. Files written elsewhere will not appear in the viewer's workspace panel.
2. **Returns the log**: It returns the run's log content. The backend reads the reply two ways: if the response is JSON with a `content` field, it uses that; otherwise it uses the raw response body text. Either is fine.
 
## Return format
 
The backend parses the reply to separate reasoning, tool calls, and the visible answer, and it picks the parser based on the content. If the reply contains the token `<|start|>`, it is routed through the CCR multi-channel parser; otherwise it is treated as the legacy XML format. The agent may return either of these:
 
* **The CCR multi-channel format** uses `<|start|>`, `<|channel|>`, and `<|message|>` delimiters — the same format the backend reads from `response.txt` files on disk. Reasoning goes on the `analysis` channel, tool calls on a `commentary to=functions.X` channel, and results in matching `functions.X` segments.
* **The legacy XML format** wraps reasoning in `<thinking>`, tool invocations in `<tool_use>`, and outputs in `<tool_result>`. This is also what the built-in simulator emits.
 
> [!IMPORTANT]
> **Developer messages are filtered automatically:** The parser drops any segment whose role is `developer` — i.e. a `<|start|>developer<|message|>...` block — so the system prompt and the tool declarations that live inside that developer segment are never shown to the user. We do not need to strip them out manually; the viewer removes them on import. This holds on both parse paths: even an otherwise-XML reply that contains a `<|start|>` token is rerouted through the multi-channel parser, so the developer block is filtered either way.
 
> [!WARNING]
> **Single Message Caveat on CCR Parse Path:** The live message API endpoint in `main.py` returns only the last parsed message block (i.e. `processed[-1]`). If the agent returns reasoning and the final response as separate `<|start|>` assistant blocks in a single query turn, the reasoning could be stranded or omitted. Ensure the agent outputs reasoning and its final message content grouped as segments of a single assistant response block, or verify the layout parses as intended.
 
## VM handoff checklist
 
These are the steps to configure and run the viewer directly on the remote VM alongside the agent:
 
First, point the backend at the VM agent's API URL before starting the server:
```bash
export CCR_API_URL="http://your-vm-ip:8080/api/chat"
```
 
Second, set the shared workspace directory so the backend and the agent write to the same path:
```bash
export CCR_WORKSPACES_DIR="/path/to/agent/runs"
```
 
Third, start the FastAPI backend bound to `0.0.0.0` so it is reachable externally:
```bash
uvicorn main:app --host 0.0.0.0 --port 8001
```
 
Fourth, update the frontend API endpoint. In `frontend/src/App.jsx`, find the `const API_BASE` line and change it to the VM's IP address:
```javascript
const API_BASE = 'http://your-vm-ip:8001';
```
 
Then start the frontend. If serving the frontend from the VM for remote access, you must pass the `--host` flag to Vite so it binds to the external network interface (otherwise it only binds to localhost and cannot be reached from your laptop):
```bash
npm run dev -- --host
```
Alternatively, build the production bundle (`npm run build`) and serve the static files.
 
## Syncing CCR runs to a local machine
 
If running the viewer on a local lab machine instead of hosting it directly on the VM, firewalls typically block the VM from initiating connection pushes (like SCP push) to your machine. To sync runs without putting a constant background network or CPU burden on the VM, use one of these two strategies:
 
### Strategy A: SSHFS Mount (Recommended — On-Demand & Zero Idle Load)
Instead of copying files back and forth, mount the remote VM directory directly to the local machine's workspace folder using **SSHFS (SSH Filesystem)**. 
 
```bash
sshfs user@your-vm-ip:/path/to/agent/runs "$CCR_WORKSPACES_DIR" -o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3
```
 
* **Why this is best:** It uses standard SSH. Files (like logs or spreadsheets) are fetched from the VM only when the browser user actually clicks on them. When the application is idle, it consumes **zero** network bandwidth and VM CPU resources.
* **Caveats:** 
  1. **Latency:** Each file preview click requires a round-trip network request to the VM, so loading a large spreadsheet may feel slightly slower than local disk reads.
  2. **Connection Drops:** If the network drops, the mount can hang. Using the `-o reconnect` flags (shown above) ensures SSHFS automatically recovers the connection.
 
### Strategy B: Trigger-Based Sync (On-Demand)
Instead of running a continuous background loop, run the sync command **only on demand** (for example, run a simple `rsync` pull script only when initiating the local viewer application or clicking a manual refresh action):
 
```bash
rsync -az --delete user@your-vm-ip:/path/to/agent/runs/ "$CCR_WORKSPACES_DIR"/
```
This restricts VM connections to active usage moments, avoiding persistent loop burden.
 
### Note on File Discovery & Frontend Refresh (Shared)
* **No File Watching**: The backend does not run a file watcher on `CCR_WORKSPACES_DIR`. Instead, it re-scans that directory dynamically on relevant API requests (e.g. when loading the list or opening a folder).
* **Manual Reload Required**: The frontend does not have a polling timer. Synced-in runs will not appear automatically on a browser tab that is already open; the user must reload the page or submit a new query to trigger a refetch. No server restart is ever required.
 
## Security note
 
The backend currently sets CORS to `allow_origins=["*"]`, which is what makes the cross-machine browser calls work out of the box. That is fine on a trusted internal network, but it allows any origin to call the API. If this is ever exposed beyond a trusted network, restrict the allowed origins to the frontend's address. The file viewer does include a path-traversal guard, so requested file paths cannot escape the workspace root.
 
## API reference
 
For completeness, the backend exposes these endpoints. The frontend uses all of them:
 
`GET /api/conversations` lists all conversations with metadata. `GET /api/conversations/{id}` returns a conversation's full structured message history. `POST /api/conversations/{id}/message` sends a prompt and is the endpoint that triggers the live-agent call described above. `GET /api/workspace/{id}/files` lists the output files in a run's workspace, and `GET /api/workspace/{id}/file/{path}` returns one file's contents — CSV/TSV parsed into a table, PDB and other text returned as previewable text, everything else reported as non-previewable.
 
## Directory layout
 
```
aicando_viewer/
├── backend/
│   ├── main.py                  API server, routing, live-agent call
│   ├── message_processor.py     Log parsing (CCR + XML formats)
│   ├── conversations/           Stored conversation JSON (auto-written)
│   └── workspaces/              Simulator output (auto-written)
└── frontend/
    ├── src/
    │   ├── App.jsx              Dashboard UI and API calls
    │   ├── index.css           Styling (no action needed)
    │   └── main.jsx            React entry point
    └── package.json
```
