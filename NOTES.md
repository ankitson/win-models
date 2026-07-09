# 2026-07-08

Qwen3.6 reasoning investigation and fix:

## Problem
When using `unsloth/unsloth/Qwen3.6-35B-A3B-GGUF` via bifrost, reasoning was not returned. Gemma models worked fine.

## Investigation chain
1. Direct raw `/v1/completions`: Qwen3.6 outputs `<think>Reasoning...</think>` as plain text tokens — model CAN reason.
2. Direct `/v1/chat/completions` via llama-server: No `reasoning_content`, no `<think>` in content.
3. Studio API via Caddy: Same — no `reasoning_content` for Qwen, but Gemma has it.

## Root causes
**Two independent problems:**

1. **`--reasoning-format deepseek` doesn't parse Qwen3.6's text-based `<think>`.** DeepSeek reasoning format expects special token IDs (e.g., DeepSeek R1 think tokens), not the plain text `<think>` that Qwen3.6 generates. Result: reasoning consumed/stripped with nothing returned.

2. **Studio's `llama_cpp.py:5574` explicitly disables thinking for Qwen3.5/3.6 models <9B active parameters.** The `extract_model_size_b()` function prefers MoE active-parameter notation (`A3B`), so Qwen3.6-35B-A3B → 3B active → `thinking_default = False`. This sets `--chat-template-kwargs {"enable_thinking": false}` on llama-server, suppressing thinking output entirely regardless of `--reasoning-format`.

With `--reasoning-format none` + `enable_thinking: true`, Qwen3.6 outputs `<think>...`
as plain text, which Studio's existing `_split_reasoning_tags_for_openai` shim in
`inference.py` properly splits into `reasoning_content` and `content`.

## Fixes

### models-config.json
- Qwen models (`qwen35b-a3b-q4kxl`, `qwen35b-a3b-q3kxl`, `qwen27b-q4km`): changed `reasoning_format` from `"deepseek"` to `"none"`.
- Gemma models unchanged (keep `"deepseek"` — Gemma's custom chat template uses tokens the deepseek parser recognizes).

### Patched Studio backend (not in this repo)
Manual patches to `C:\Users\ankit\Documents\docs-root\projects\code\unsloth\studio\backend\core\inference\llama_cpp.py`:
- Line 5577: `thinking_default = False` → `thinking_default = True` (for Qwen3.5/3.6 <9B guard)
- Line 5586: `reasoning_kw["preserve_thinking"] = False` → `True`

NOTE: These patches are NOT in source control (Studio installation). Need a permanent shim in `unsloth.py` to survive Studio updates.

### edge.py
- Fixed `_apply_default_ports()` to set `port=` default (was only setting `studio_port=`). This makes `just stop` kill processes on port 8888 (Studio), which was previously missed — stale Python Studio processes would prevent patched code from loading on restart.

## Result
Qwen3.6 now returns `reasoning_content` by default via the OpenAI API without any per-request `chat_template_kwargs`. Tested: 2222 chars reasoning + 150 chars content for "Think step by step: 7*13?"



# 2026-07-06

What I learnt today:
- the several layers of convention and formatting on how to represent thinking in LLMS


## Base layer
There is the BASE layer - a model speaks only text. There are 2 approaches to represent thinking:

### Deepseek <think> style
The model was RL-ed to output thinking surrounded by `What<think>xxx</think>`. It literally outputs the tokens for `'<', 't','h','i','n','k','>'`.

### Llama style
They use a special token in the tokenizer itself to represent say a <think>. 

Code example from `llama.cpp` issue (https://github.com/ggml-org/llama.cpp/discussions/9379#discussioncomment-10587529):

```python
text: '<|im_start|>Hello World<|im_end|>'

parse_special == false:
	    27 ('<')
	    91 ('|')
	   318 ('im')
	  4906 ('_start')
	    91 ('|')
	    29 ('>')
	  9707 ('Hello')
	  4337 (' World')
	    27 ('<')
	    91 ('|')
	   318 ('im')
	  6213 ('_end')
	    91 ('|')
	    29 ('>')

parse_special  == true:
	151644 ('<|im_start|>')
	  9707 ('Hello')
	  4337 (' World')
	151645 ('<|im_end|>')
```

Gemma 4 sample:
```xml
<bos><|turn>system
<|think|>
<turn|>
<|turn>user
What is the water formula?<turn|>
<|turn>model
The term "wat
```

Each of those `<bos>`, `<|turn>` etc. are just textual representations of a single special control token.
