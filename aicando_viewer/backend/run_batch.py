"""
Batch-run disease prompts through GPT-OSS-120B on CCR.

Reads   prompts/*.txt          one prompt per file; filename = disease key
Writes  results/<key>.txt      final channel (your JSON)
        results/<key>.raw.txt  unparsed reply, only if parsing failed
        results/<key>.analysis.txt   reasoning channel (optional)
        done.log               drives resume

Model loading and harmony handling follow wmangion's run_gpt_oss.py.
Differences: no request queue, no spec file, prompts are batched.
"""

import os, sys, glob, json, time, traceback
from pathlib import Path

# ---- paths -------------------------------------------------------------
PROJECT  = Path("/projects/academic/rams/dshah22")
MODEL    = "/projects/academic/rams/wmangion/aicando/models/gpt-oss-120b"
PROMPTS  = PROJECT / "prompts"
RESULTS  = PROJECT / "results"
DONE_LOG = PROJECT / "done.log"

# ---- tunables ----------------------------------------------------------
BATCH_SIZE     = 4       # prompts per generate() call; drop to 2 on OOM
MAX_NEW_TOKENS = 10000   # your responses run ~6-8k including reasoning
TEMPERATURE    = 0.7     # same as his script
SAVE_ANALYSIS  = True

# ---- keep caches off the 25GB home dir (his setup_environment) ---------
CACHE = PROJECT / "cache"
TMP   = PROJECT / "tmp"
for d in (CACHE, TMP, RESULTS):
    d.mkdir(parents=True, exist_ok=True)
os.environ["HF_HOME"] = str(CACHE)
os.environ["HUGGINGFACE_HUB_CACHE"] = str(CACHE / "hub")
os.environ["TMPDIR"] = str(TMP)

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from openai_harmony import (
    load_harmony_encoding, HarmonyEncodingName,
    Role, Message, Conversation,
)


def load_model():
    print(f"CUDA available: {torch.cuda.is_available()}", flush=True)
    if not torch.cuda.is_available():
        sys.exit("no GPU visible - check --gpus-per-node in the sbatch header")
    print(f"CUDA device count: {torch.cuda.device_count()}", flush=True)

    enc = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
    tokenizer = AutoTokenizer.from_pretrained(MODEL)

    # batching needs a pad token and left padding so every sequence
    # ends at the same index
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(
        MODEL,
        torch_dtype=torch.bfloat16,
        device_map={"": 0},
        trust_remote_code=True,
        low_cpu_mem_usage=True,
    )
    model.eval()
    return model, tokenizer, enc


def render_prompt(enc, prompt_text):
    """Single user turn -> harmony-formatted text, his decode path."""
    convo = Conversation.from_messages([
        Message.from_role_and_content(Role.USER, prompt_text)
    ])
    result = enc.render_conversation_for_completion(convo, Role.ASSISTANT)
    if isinstance(result, str):
        return result
    if hasattr(enc, "decode"):
        return enc.decode(result)
    if hasattr(enc, "tokenizer") and hasattr(enc.tokenizer, "decode"):
        return enc.tokenizer.decode(result)
    raise ValueError("cannot decode harmony render result")


def parse_channels(enc, response_tokens):
    """
    Split the reply into (final, analysis).

    Note: his script un-escapes \\n and \\t in the content. That is skipped
    here on purpose -- your prompts ask for raw JSON, and un-escaping would
    turn legitimate \\n inside JSON strings into real newlines, which is
    invalid JSON.
    """
    msgs = enc.parse_messages_from_completion_tokens(
        response_tokens, role=Role.ASSISTANT)

    final_parts, analysis_parts = [], []
    for m in msgs:
        content = getattr(m, "content", None)
        if content is None:
            content = getattr(m, "text", None)
        if isinstance(content, list):
            if not content:
                content = ""
            elif isinstance(content[0], str):
                content = "\n".join(content)
            else:
                content = "\n".join(
                    getattr(c, "text", str(c)) for c in content)
        if content is None:
            continue
        if not isinstance(content, str):
            content = str(content)

        channel = getattr(m, "channel", None)
        channel = str(channel).lower() if channel else ""
        if channel == "analysis":
            analysis_parts.append(content)
        elif channel in ("final", ""):
            final_parts.append(content)
        # commentary is dropped

    return ("\n".join(final_parts).strip() or None,
            "\n".join(analysis_parts).strip() or None)


def load_pending():
    done = set()
    if DONE_LOG.exists():
        done = {l.strip() for l in DONE_LOG.read_text().splitlines() if l.strip()}
    pending = []
    for p in sorted(glob.glob(str(PROMPTS / "*.txt"))):
        key = Path(p).stem
        if key not in done:
            pending.append((key, Path(p).read_text(encoding="utf-8")))
    return pending, len(done)


def save(key, final, analysis, raw):
    """Write results and mark done. Returns True if output parsed as JSON."""
    ok = False
    if final:
        (RESULTS / f"{key}.txt").write_text(final, encoding="utf-8", newline="")
        try:
            json.loads(final)
            ok = True
        except json.JSONDecodeError:
            pass
    if not ok:
        # keep the unparsed reply so nothing is lost
        (RESULTS / f"{key}.raw.txt").write_text(raw, encoding="utf-8", newline="")
    if SAVE_ANALYSIS and analysis:
        (RESULTS / f"{key}.analysis.txt").write_text(
            analysis, encoding="utf-8", newline="")
    with open(DONE_LOG, "a") as f:
        f.write(key + "\n")
        f.flush()
        os.fsync(f.fileno())
    return ok


def main():
    pending, already = load_pending()
    print(f"{already} already done, {len(pending)} pending", flush=True)
    if not pending:
        return

    t0 = time.time()
    model, tokenizer, enc = load_model()
    print(f"model loaded in {time.time()-t0:.0f}s", flush=True)

    n = good = 0
    for i in range(0, len(pending), BATCH_SIZE):
        chunk = pending[i:i + BATCH_SIZE]
        texts = [render_prompt(enc, text) for _, text in chunk]

        inputs = tokenizer(texts, return_tensors="pt",
                           padding=True).to(model.device)
        in_len = inputs["input_ids"].shape[1]

        t1 = time.time()
        try:
            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=MAX_NEW_TOKENS,
                    do_sample=True,
                    temperature=TEMPERATURE,
                    pad_token_id=tokenizer.pad_token_id,
                )
        except torch.cuda.OutOfMemoryError:
            print(f"OOM on batch of {len(chunk)} - lower BATCH_SIZE", flush=True)
            torch.cuda.empty_cache()
            continue
        except Exception:
            traceback.print_exc()
            torch.cuda.empty_cache()
            continue
        dt = time.time() - t1

        for j, (key, _) in enumerate(chunk):
            resp_tokens = outputs[j][in_len:].tolist()
            raw = tokenizer.decode(resp_tokens, skip_special_tokens=False)
            try:
                final, analysis = parse_channels(enc, resp_tokens)
            except Exception as e:
                print(f"  parse failed for {key}: {e}", flush=True)
                final, analysis = None, None
            ok = save(key, final, analysis, raw)
            n += 1
            good += ok
            print(f"[{n}/{len(pending)}] {key}  {dt/len(chunk):.0f}s/prompt"
                  + ("" if ok else "   NOT VALID JSON -> .raw.txt"), flush=True)

        torch.cuda.empty_cache()

    mins = (time.time() - t0) / 60
    print(f"\ndone: {n} processed, {good} valid JSON, {mins:.1f} min", flush=True)
    if n:
        print(f"projected for 2000: {mins/n*2000/60:.1f} hours", flush=True)


if __name__ == "__main__":
    main()
