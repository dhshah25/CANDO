"""
Message Processor for AICANDO Conversation Viewer.
 
Handles parsing and transformation of raw LLM conversation logs into
structured message objects consumed by the frontend.

Supports two log formats:
  A) CCR multi-channel format (response.txt with <|start|> delimiters)
  B) Legacy XML format (<thinking>, <tool_use>, <tool_result> tags)

Processing pipeline for each message:
  1. Chain-of-Thought (CoT) extraction — isolates internal reasoning
  2. Tool call parsing — converts raw function calls to readable summaries
  3. Tool result pairing — matches results back to their originating calls
  4. File reference extraction — detects workspace paths for UI linking
  5. Developer message filtering — hides system prompts
"""

import re
import json
from typing import Dict, List, Tuple, Optional


# =====================================================================
# A. CCR FORMAT PARSER (response.txt with multi-channel tokens)
# =====================================================================

# Delimiter tokens used by the CCR agent
_START     = "<|start|>"
_END       = "<|end|>"
_MSG       = "<|message|>"
_CHAN      = "<|channel|>"
_CALL      = "<|call|>"
_RETURN    = "<|return|>"
_CONSTRAIN = "<|constrain|>"


def parse_response_txt(raw_text: str) -> List[Dict]:
    """
    Parse a raw response.txt file into a list of processed message dicts.

    Segments are classified by role and channel:
      - developer        → skipped (system prompts)
      - user             → user message
      - assistant/analysis → chain-of-thought (hidden by default)
      - assistant/commentary to=functions.X → tool call
      - functions.X      → tool result (paired with preceding call)
      - assistant (no channel) → visible response
    """
    segments = _tokenize_ccr(raw_text)

    messages = []
    pending_cot = []     # Accumulates reasoning segments
    pending_tools = []   # Accumulates tool call/result pairs

    for seg in segments:
        role = seg["role"]
        channel = seg.get("channel")
        content = seg["content"]

        # Skip developer/system messages
        if role == "developer":
            continue

        # User message — flush any pending reasoning/tools first
        if role == "user":
            _flush_pending(messages, pending_cot, pending_tools)
            pending_cot = []
            pending_tools = []
            messages.append({
                "role": "user",
                "content": content.strip(),
                "chain_of_thought": None,
                "tool_calls": [],
                "files": [],
            })
            continue

        # Tool result from functions.X — pair with the matching tool call
        if role.startswith("functions."):
            tool_name = role.split(".", 1)[1] if "." in role else role
            paired = False
            # Try to match by name first
            for pt in reversed(pending_tools):
                if pt["type"] == "tool_call" and pt["name"] == tool_name and "result" not in pt:
                    pt["result"] = content.strip()[:500]
                    paired = True
                    break
            # Fallback: pair with any unmatched tool call
            if not paired:
                for pt in reversed(pending_tools):
                    if pt["type"] == "tool_call" and "result" not in pt:
                        pt["result"] = content.strip()[:500]
                        paired = True
                        break
            # No matching call found — store as standalone result
            if not paired:
                pending_tools.append({
                    "type": "tool_result",
                    "name": tool_name,
                    "content": content.strip()[:500],
                })
            continue

        # Assistant analysis channel → internal reasoning (CoT)
        if role == "assistant" and channel == "analysis":
            pending_cot.append(content.strip())
            continue

        # Assistant commentary to functions.X → tool call invocation
        if role == "assistant" and channel and channel.startswith("commentary to=functions."):
            func_name = channel.replace("commentary to=functions.", "").strip()
            json_str = content.strip()
            try:
                args = json.loads(json_str)
            except json.JSONDecodeError:
                args = {"raw": json_str}
            pending_tools.append({
                "type": "tool_call",
                "name": func_name,
                "arguments": args,
            })
            continue

        # Assistant visible response (no channel)
        if role == "assistant":
            cot_text = "\n\n".join(pending_cot) if pending_cot else None
            tools = list(pending_tools)
            pending_cot = []
            pending_tools = []

            clean_content = content.strip()
            raw_paths = extract_file_references(clean_content)
            file_refs = [clean_file_path(p) for p in raw_paths]

            messages.append({
                "role": "assistant",
                "content": clean_content,
                "chain_of_thought": cot_text,
                "tool_calls": tools,
                "files": file_refs,
            })
            continue

    # Flush any trailing reasoning/tools from the end of the log
    _flush_pending(messages, pending_cot, pending_tools)

    return messages


def _flush_pending(messages: List[Dict], pending_cot: List[str], pending_tools: List[Dict]):
    """Attach any remaining CoT/tools to the last assistant message."""
    if not pending_cot and not pending_tools:
        return
    for msg in reversed(messages):
        if msg["role"] == "assistant":
            if pending_cot and not msg["chain_of_thought"]:
                msg["chain_of_thought"] = "\n\n".join(pending_cot)
            if pending_tools:
                msg["tool_calls"].extend(pending_tools)
            break


def _parse_segment(part: str) -> Optional[Dict]:
    """
    Parse a single segment (text between <|start|> tokens) into a dict
    with keys: role, channel (optional), content.

    Handles three layout patterns:
      1. role<|message|>content
      2. role<|channel|>channel_name<|message|>content
      3. assistant<|message|><|channel|>analysis<|message|>content
    """
    part = part.strip()
    if not part:
        return None

    # Strip trailing control tokens
    part = part.replace(_RETURN, "").rstrip()
    if part.endswith(_END):
        part = part[:-len(_END)].rstrip()
    if part.endswith(_CALL):
        part = part[:-len(_CALL)].rstrip()

    # Pattern 3: assistant<|message|><|channel|>...<|message|>content
    pat3 = f"assistant{_MSG}{_CHAN}"
    if part.startswith(pat3):
        rest = part[len(pat3):]
        msg_idx = rest.find(_MSG)
        if msg_idx != -1:
            channel = rest[:msg_idx].strip()
            content = rest[msg_idx + len(_MSG):].strip()
            return {"role": "assistant", "channel": channel, "content": content}

    # Pattern 2: role<|channel|>channel_name<|message|>content
    chan_idx = part.find(_CHAN)
    msg_idx = part.find(_MSG)

    if chan_idx != -1 and (msg_idx == -1 or chan_idx < msg_idx):
        role = part[:chan_idx].strip()
        rest = part[chan_idx + len(_CHAN):]
        next_msg_idx = rest.find(_MSG)
        if next_msg_idx != -1:
            channel_raw = rest[:next_msg_idx].strip()
            if _CONSTRAIN in channel_raw:
                channel_raw = channel_raw.split(_CONSTRAIN)[0].strip()
            content = rest[next_msg_idx + len(_MSG):].strip()
            return {"role": role, "channel": channel_raw, "content": content}

    # Pattern 1: role<|message|>content
    if msg_idx != -1:
        role = part[:msg_idx].strip()
        content = part[msg_idx + len(_MSG):].strip()
        return {"role": role, "channel": None, "content": content}

    return None


def _tokenize_ccr(raw: str) -> List[Dict]:
    """Split raw text on <|start|> tokens and parse each segment."""
    segments = []
    for part in raw.split(_START):
        seg = _parse_segment(part)
        if seg:
            segments.append(seg)
    return segments


def clean_file_path(path: str) -> str:
    """Convert absolute workspace paths to relative ones for display."""
    match = re.search(r'/workspaces?/[^/]+/(.*)', path)
    if match:
        return match.group(1)
    return path


# =====================================================================
# B. LEGACY FORMAT SUPPORT (XML-style tags in message content)
# =====================================================================

# Patterns that indicate chain-of-thought blocks
COT_PATTERNS = [
    (r'<thinking>(.*?)</thinking>', re.DOTALL),
    (r'<scratchpad>(.*?)</scratchpad>', re.DOTALL),
    (r'<reasoning>(.*?)</reasoning>', re.DOTALL),
    (r'<internal>(.*?)</internal>', re.DOTALL),
    (r'<chain_of_thought>(.*?)</chain_of_thought>', re.DOTALL),
    (r'\[THINKING\](.*?)\[/THINKING\]', re.DOTALL),
    (r'\[COT\](.*?)\[/COT\]', re.DOTALL),
]


def strip_chain_of_thought(text: str) -> Tuple[str, Optional[str]]:
    """
    Extract and remove chain-of-thought blocks from message text.

    Returns:
        (clean_text, cot_text) — visible content and extracted reasoning.
        cot_text is None if no CoT blocks were found.
    """
    cot_fragments = []
    clean = text

    for pattern, flags in COT_PATTERNS:
        matches = re.findall(pattern, clean, flags)
        if matches:
            cot_fragments.extend(matches)
            clean = re.sub(pattern, '', clean, flags=flags)

    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
    cot_combined = '\n\n'.join(f.strip() for f in cot_fragments) if cot_fragments else None
    return clean, cot_combined


# =====================================================================
# TOOL CALL PARSER (Legacy XML/JSON format)
# =====================================================================

def parse_tool_calls(text: str) -> Tuple[str, List[Dict]]:
    """
    Extract <tool_use> and <tool_result> blocks from message text.
    Results are paired with their corresponding calls by index.

    Returns:
        (clean_text, tool_calls) — text with tags removed, and parsed tool data.
    """
    tool_calls = []
    clean = text

    # Parse <tool_use> blocks (JSON containing name + arguments)
    tool_use_pattern = re.compile(r'<tool_use>(.*?)</tool_use>', re.DOTALL)
    for match in tool_use_pattern.finditer(clean):
        content = match.group(1).strip()
        try:
            parsed = json.loads(content)
            tool_calls.append({
                "type": "tool_call",
                "name": parsed.get("name", "tool"),
                "arguments": parsed.get("input", parsed.get("arguments", {})),
            })
            clean = clean.replace(match.group(0), f'[[TOOL_CALL:{len(tool_calls)-1}]]')
        except json.JSONDecodeError:
            pass

    # Parse <tool_result> blocks and pair with preceding calls
    tool_result_pattern = re.compile(r'<tool_result>(.*?)</tool_result>', re.DOTALL)
    tool_results = list(tool_result_pattern.finditer(clean))
    for idx, match in enumerate(tool_results):
        content = match.group(1).strip()
        if idx < len(tool_calls):
            tool_calls[idx]["result"] = content[:500]
        else:
            tool_calls.append({
                "type": "tool_result",
                "name": "result",
                "content": content[:500],
            })
        clean = clean.replace(match.group(0), f'[[TOOL_RESULT:{idx}]]')

    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
    return clean, tool_calls


# =====================================================================
# FILE REFERENCE EXTRACTION
# =====================================================================

FILE_PATH_PATTERN = re.compile(
    r'(?:^|[\s`"\'])(/[\w./-]+\.(?:csv|tsv|txt|json|py|md|log|html|pdf|pdb|out))(?:\s|`|$)',
    re.MULTILINE
)


def extract_file_references(text: str) -> List[str]:
    """Extract file paths mentioned in message text for workspace linking."""
    return list(set(FILE_PATH_PATTERN.findall(text)))


# =====================================================================
# MAIN PROCESSOR — COMBINES ALL TRANSFORMATIONS
# =====================================================================

def process_message(message: Dict) -> Dict:
    """
    Process a single message through all transformation layers.
    Used for legacy JSON conversations and simulator output.

    Pipeline: CoT extraction → tool parsing → file detection → cleanup.
    """
    result = {
        "role": message.get("role", "unknown"),
        "content": message.get("content", ""),
        "chain_of_thought": None,
        "tool_calls": [],
        "files": [],
    }

    # Only assistant messages need processing
    if result["role"] != "assistant":
        return result

    text = result["content"]

    # If the content is in CCR multi-channel log format, parse it using the CCR parser
    if _START in text:
        parsed = parse_response_txt(text)
        if parsed:
            for p_msg in reversed(parsed):
                if p_msg.get("role") == "assistant":
                    return p_msg
            return parsed[-1]

    # Step 1: Extract chain-of-thought
    text, cot = strip_chain_of_thought(text)
    result["chain_of_thought"] = cot

    # Step 2: Parse tool calls
    text, tools = parse_tool_calls(text)
    result["tool_calls"] = tools

    # Step 3: Extract file references
    result["files"] = extract_file_references(text)

    # Step 4: Clean up internal placeholders
    text = re.sub(r'\[\[TOOL_(?:CALL|RESULT):\d+\]\]\s*', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    result["content"] = text

    return result


def process_conversation(messages: List[Dict]) -> List[Dict]:
    """Process all messages in a conversation through the pipeline."""
    return [process_message(msg) for msg in messages]
