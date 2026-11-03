# Splaude vs llama.cpp for Claude Code

Same Mac, same model, same weights file. Speed numbers are from
2026-10-21; the larger-than-memory numbers are from 2026-11-01 to 2026-11-03.

## Setup

| | llama.cpp | Splash |
|---|---|---|
| Mac | M4 Pro, 48 GB | M4 Pro, 48 GB |
| Model | Qwen3.8-27B `UD-Q4_K_XL` (Unsloth v3) | the same GGUF file |
| Engine | llama.cpp b11493 (`08246a28`) + id_slot patch | Splash 1.4.0 (Homebrew) |
| Speculation | none, or MTP draft (`draft-mtp`, n-max 2) | DFlash2 draft (`incoai/Qwen3.8-27B-DFlash2`) |
| KV cache | q8_0 | int8 |
| Context | 262,144 | 262,144 |
| Slots | 1 | 1 |

Each engine ran alone on the machine with nothing else served. Per context
depth: one cold request (new prefix, measures prefill), then two warm requests
per output shape (prefix cached, about 500 new tokens, measures decode at that
depth). Output shapes:
- **prose:** free text;
- **copy:** text largely repeated from the prompt, as in code edits.

## Decode, tokens per second

| Depth | llama.cpp | llama.cpp + MTP | Splash | vs llama.cpp | vs llama.cpp + MTP |
|---:|---:|---:|---:|---:|---:|
| 2k, prose | 10.3 | 15.0 | 29.4 | 2.9x | 2.0x |
| 20k, prose | 9.5 | 12.6 | 26.9 | 2.8x | 2.1x |
| 60k, prose | 8.3 | 9.3 | 23.8 | 2.9x | 2.6x |
| 2k, copy | 10.4 | 38.8 | 36.6 | 3.5x | 0.9x |
| 20k, copy | 9.5 | 33.9 | 33.8 | 3.6x | 1.0x |
| 60k, copy | 8.4 | 28.3 | 36.2 | 4.3x | 1.3x |

Splash figures are the mean of its two warm requests.

## Prefill, tokens per second (cold)

| Depth | llama.cpp | llama.cpp + MTP | Splash | vs llama.cpp + MTP |
|---:|---:|---:|---:|---:|
| 2k | 113.0 | 112.2 | 251 | 2.2x |
| 20k | 102.8 | 96.7 | 222 | 2.3x |
| 60k | 79.6 | 73.1 | 177 | 2.4x |

A cold 60k prompt: about 350 s on Splash, about 780 s on llama.cpp.

## Memory, wired GB during the run

| Depth | llama.cpp + MTP | Splash | Splash uses less |
|---:|---:|---:|---:|
| 2k | 32.9 | 25.1 | 7.8 |
| 20k | 33.2 | 26.3 | 6.9 |
| 60k | 32.7 | 29.3 | 3.4 |

## Claude Code session

Claude Code against Splash's Anthropic Messages endpoint, three turns with tool
calls (Read, Edit, Bash); the edits land. At 11-12k tokens of context:

| Turn | Cached | Time to first token | Decode tok/s |
|---|---:|---:|---:|
| 1, cold system prompt | 0 | 49.6 s | 31.7 |
| 1, after tool result | 10,944 | 3.2 s | 44.7 |
| 2 | 11,296 / 11,456 | 1.9 / 2.6 s | 51.4 / 45.4 |
| 3 | 11,712 / 11,840 | 1.8 / 2.2 s | 41.8 / 41.6 |

Every follow-up reused 98% or more of the prompt from cache. At 220k tokens of
context, a warm turn's first token came after 12.4 s, with decode at 32.7 tok/s.

## Models larger than memory

Qwen3.8-Flash-Next `GSQ-RCO IQ3_S` (83.6 GB) on the same 48 GB M4 Pro runs on
Splaude with experts streamed from the SSD. Measured over full, multi-hour
Claude Code runs (tool loop, real repositories): median decode 23.5 to 29.5
tok/s across four runs.

## Caveats

- Splash ran with thinking on during the speed tests (its default then). Rates
  per token are comparable; total turn time with thinking off is lower.
- One machine, one model. Splash's speedup comes from the DFlash2 draft and
  per-model kernels, so other models and Macs will differ.
- Quality was not compared here. Splash's next-token agreement with llama.cpp
  is in [performance.md](performance.md#gguf-against-llamacpp).
