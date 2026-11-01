# Splash

## About this fork: splash-claude

This fork of [incoai/splash](https://github.com/incoai/splash) is here so that
Splash can be used with [Claude Code](https://docs.anthropic.com/en/docs/claude-code),
for as long as upstream Splash doesn't cover that yet. The fixes it carries, and
where each one stands upstream:

| Fix | Option | Upstream |
|---|---|---|
| Sampling defaults for requests that set none | `--sampling-default NAME=VALUE` | [#614](https://github.com/incoai/splash/pull/614) |
| No request reasons, whatever it asks | `--reasoning off` | [#615](https://github.com/incoai/splash/pull/615) |
| Exit instead of relaunching a failed engine | `--engine-failure exit` | [#617](https://github.com/incoai/splash/pull/617) |
| Per-request sampling from headers, for clients that cannot set it in the body | `--sampling-headers` | [#618](https://github.com/incoai/splash/pull/618) |
| A supervisor's check before every engine spawn | `--spawn-guard COMMAND` | [#637](https://github.com/incoai/splash/pull/637) |
| llama.cpp-shaped `/slots` and `/props`, and requests pinned to a slot by `id_slot`, for proxies such as llama-swap | `--llama-cpp-compat` | [#638](https://github.com/incoai/splash/pull/638) |
| Serve any model name with the local model | `--accept-any-model` | fork only |
| Name the served model file for a supervisor | `-m` / `--model-file PATH` | fork only |

Launching a new engine only once the old one has exited started here too, and is
now in upstream Splash ([#616](https://github.com/incoai/splash/pull/616)).

### Claude Code against Splash, including headless and long runs

Start the server with the fork's Claude Code options:

```bash
splash-claude serve --model unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M \
  --accept-any-model --reasoning off --sampling-headers \
  --sampling-default temperature=0.5 --sampling-default top_p=0.8 \
  --sampling-default top_k=20
```

Then point Claude Code at it, interactive or with `-p`:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8000
export ANTHROPIC_AUTH_TOKEN=local
export API_TIMEOUT_MS=86400000
export CLAUDE_STREAM_IDLE_TIMEOUT_MS=86400000
export CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK=1

claude --model local                     # interactive
claude --model local -p "your task"      # headless
```

Why each setting:

| Setting | Why |
|---|---|
| `--accept-any-model` | Claude Code sends its own model names, also for side requests such as titles and summaries. Every name is served by the loaded model. |
| `--reasoning off` | Claude Code asks for thinking on every turn. Without this, every turn reasons. |
| `--sampling-default` | Claude Code sends no sampling fields, so these are the values every request gets. Without them the model's generation config applies. |
| `--sampling-headers` | Lets a run set its own sampling through headers: `ANTHROPIC_CUSTOM_HEADERS=$'X-Temperature: 0.7\nX-Presence-Penalty: 0.5'`. |
| `ANTHROPIC_AUTH_TOKEN` | Any value. Splash does not check it; Claude Code needs one to skip its login. |
| `API_TIMEOUT_MS`, `CLAUDE_STREAM_IDLE_TIMEOUT_MS` | A prefill of a long context can take many minutes before the first token. With the default limits the client gives up first. |
| `CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK=1` | After a stream error Claude Code retries without streaming. That request gets no keepalive and hits the client's time limit on a long prompt. |

For a server started with another `--port`, use that port in
`ANTHROPIC_BASE_URL`.

It works together with
[llama-swap-macos-extended](https://github.com/pcvelz/llama-swap-macos-extended),
a proxy that switches between Splash and llama.cpp. The rest of this README is
upstream's.

[![CI](https://github.com/pcvelz/splash-claude/actions/workflows/ci.yml/badge.svg)](https://github.com/pcvelz/splash-claude/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Apple%20silicon-black.svg)](#quick-start)

**A local inference engine for Apple silicon, built around the model.**

Splash runs coding agents and OpenAI or Anthropic compatible applications on
one Mac. It combines [DFlash 2](https://inco.ai/blog/dflash2/) speculative
decoding, specialized Metal kernels, and automatic memory planning, with
vision, tool calling, and a built-in chat page.
It reuses cached prefixes and batches concurrent requests automatically.

## Quick start

Install the command line from [pcvelz/splash-claude](https://github.com/pcvelz/splash-claude)
on an Apple M3 or newer with macOS 26.4 or later:

```bash
brew trust --formula pcvelz/tap/splash-claude
brew install pcvelz/tap/splash-claude
splash-claude serve --model unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M
```

The first run downloads the model and its matching draft and starts serving
on `127.0.0.1:8000`. Later starts reuse the downloads; leave room on disk for
them ([storage requirements](docs/server.md#model-storage)).

Once it prints `Ready`, leave this terminal open. Open <http://127.0.0.1:8000>
in your browser, or run an installed coding agent from another terminal:

```bash
splash-claude claude    # or: splash-claude opencode / splash-claude codex / splash-claude hermes / splash-claude pi
```

To start Claude Code directly against Splash's default port instead:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8000 claude
```

For a server started with another `--port`, pass the same `--port` to the agent
or use that port in `ANTHROPIC_BASE_URL`.

Press Ctrl+C in the server terminal to stop Splash.
For LM Studio Bionic, follow its [Splash setup guide](https://lmstudio.ai/blog/splash-engine).

## Use the API

OpenAI Chat Completions, Responses and Completions, and Anthropic Messages,
with streaming, tool calls, JSON Schema output, and base64 images and PDFs:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M",
    "messages": [{"role": "user", "content": "Explain speculative decoding in one sentence."}]
  }'
```

Reasoning follows the model default; `"reasoning_effort": "none"` turns it off
in Chat. Anthropic Messages requests reason only when they set `thinking`.
[Reasoning settings](docs/server.md#default-reasoning-effort) ·
[Tool calls](docs/api.md#tool-calls) ·
[API details](docs/api.md)

## Models

Splash supports these model families, with a matching DFlash2 draft selected
automatically:

| Model | GGUF example | MLX 4-bit |
| --- | --- | --- |
| Qwen3.8-27B | `unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M` | `mlx-community/Qwen3.8-27B-4bit` |
| Qwen3.6-35B-A3B | `unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M` | `mlx-community/Qwen3.6-35B-A3B-4bit` |
| Ornith-1.5-122B | `unsloth/Ornith-1.5-122B-GGUF:UD-Q4_K_XL` | `ornith-ai/Ornith-1.5-122B-MLX-4bit` |
| Qwen3.8-Flash-Next | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF:Q2_0` | — |
| Flint | `unsloth/flint-GGUF:UD-Q4_K_XL` | — |

Unsloth GGUF variants span **1–8 bits**, including mixed-precision UD formats
up to `UD-Q8_K_XL`, whose BF16 tensors load too. Whole-BF16 and F16 models are
not supported: the installer refuses a GGUF whose projections and experts are
all BF16, F16 or F32.
[Prism ML Ternary Bonsai 2](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf)
is also supported in PQ2_0 (7.2 GB), including vision. Pass `OWNER/REPO:VARIANT`
to `--model`, as in the quick start. MLX targets can be affine 2–8-bit,
including mixed precision, mxfp4 or nvfp4. NVIDIA's and unsloth's NVFP4
checkpoints and NVFP4 GGUFs also load. Smaller variants run on
[24 GB Macs](docs/performance.md#smaller-ggufs-on-24-gb-macs).
[27B variants](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main) ·
[35B variants](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/tree/main) ·
[Ornith-1.5-122B variants](https://huggingface.co/unsloth/Ornith-1.5-122B-GGUF/tree/main) ·
[Flash-Next variants](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/tree/main)

Vision and the tokenizer come from the target model's source.
[Model loading and compatibility](docs/models.md#upstream-model-loading) ·
[Supported formats](docs/models.md#gguf-targets)

To serve files already on this Mac, pass the target's GGUF file or GGUF/MLX
directory directly, and a compatible local DFlash2 draft:

```bash
splash-claude serve --model local/qwen \
  --model-dir /models/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_M.gguf \
  --draft-model /models/Qwen3.8-27B-DFlash2
```

This prepares local files without contacting the Hub; `--model` is the name
reported by the API. Omit `--draft-model` to use the family's draft from the
Hub cache, or add `--mmproj /models/mmproj-BF16.gguf` for a GGUF's vision.
See [local model files](docs/models.md#local-model-files) for details.

## Settings

Memory and context are sized automatically, up to the model's native context
window. To set your own limits or cache options, add these to `splash-claude serve`:

| Option | Purpose |
| --- | --- |
| `--max-memory 28G` | Cap Metal memory use. |
| `--idle-release off` | Keep the model in memory while idle (default: release after 10m). |
| `--max-context 100K` | Set the context limit. Up to `1M` tokens. |
| `--language-only` | Skip vision; serve text only. |
| `--kv-format bf16` | Use BF16 KV cache. Default: 8-bit (INT8). |
| `--state-format bf16` | Use BF16 recurrent states, half the size. Default: FP32. |
| `--disable-ane` | Prefill on the GPU alone. Default: the 27B also uses the Neural Engine. |
| `--max-cache-disk 16G` | Cap the SSD cache (default: auto, up to 32 GiB; `0` turns it off). |
| `--persistent-cache` | Keep the SSD cache across restarts. Off by default. |

Use `--max-memory` to leave room for other applications.
The server listens on localhost without authentication by default. For LAN
access, authentication, browser apps on other origins, and other options, see
[server configuration](docs/server.md#server-configuration) or
`splash-claude serve --help`.
[KV precision](docs/server.md#kv-cache-precision) ·
[SSD cache](docs/server.md#ssd-cache) ·
[Advanced memory settings](docs/server.md#memory-and-context)

## Performance

Measured with Splash 1.0 on an M5 Pro (16-core GPU, 48 GB), using its model
packages and selected SPEED-Bench coding prompts over HTTP. Ratios compare
with the next-fastest engine measured in that benchmark. Qwen3.8-Flash-Next
(`ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF:Q2_0`) was measured the same way
with this release on an M5 Pro (20-core GPU, 64 GB); its ratios compare with
Strata serving the same GGUF on that Mac.

| Metric | Qwen3.6-35B-A3B | Qwen3.8-27B | Qwen3.8-Flash-Next |
| --- | ---: | ---: | ---: |
| Decode · short prompt | 210 tok/s (1.7×) | 74 tok/s (2.0×) | 95 tok/s (1.3×) |
| Prefill · 32K prompt | 2,011 tok/s (1.3×) | 363 tok/s (1.2×) | 1,312 tok/s (1.3×) |
| Cached time to first token · 32K replay | 123 ms (6.6×) | 282 ms (7.3×) | 171 ms (212×) |
| Aggregate decode · 4 concurrent short prompts | 357 tok/s (2.0×) | 170 tok/s (3.9×) | 171 tok/s (2.4×) |

[Launch benchmarks](https://inco.ai/blog/splash/) ·
[Measurement details](docs/performance.md#splash-10-launch-benchmarks) ·
[Run benchmarks locally](docs/validate.md#local-benchmarks)

### GGUF against llama.cpp

Same Unsloth UD-Q4_K_M weights on Metal. Decode speed in tok/s:

| Model | Engine | M5 Pro, 20-core GPU | M3 Max, 40-core GPU |
| --- | --- | ---: | ---: |
| 27B | llama.cpp | 16 | 17 |
| | llama.cpp with MTP | 27 | 20 |
| | **Splash** | **74** | **92** |
| 35B-A3B | llama.cpp | 69 | 66 |
| | **Splash** | **175** | **209** |

That is **2.5–3.2×** as fast on the 35B and **4.5–5.3×** on the 27B
(**2.7–4.6×** against MTP).

**Closely matches llama.cpp's predictions.**

| Next-token agreement ↑ | 27B | 35B-A3B |
| --- | ---: | ---: |
| llama.cpp: CPU vs. GPU | 97.8% | 96.5–96.9% |
| llama.cpp: single-token vs. batched | 99.65–99.75% | 97.95% |
| **Splash vs. llama.cpp** | **99.30–99.45%** | **97.83–98.14%** |

Splash uses BF16 KV and `--disable-ane` in this comparison.
[Benchmark details](docs/performance.md#gguf-against-llamacpp)

## Design

Each supported model pairs a trained DFlash2 draft with Metal kernels for its
shapes. The runtime, scheduler, cache, and API are shared. Weights are
converted to the kernels' layouts as they load, with no copy on disk; kernels
ship precompiled, with no Xcode or local tuning required.
[How Splash works](https://inco.ai/blog/splash/)

## More

- [Documentation](docs/README.md): configuration, API, internals, building, and testing.
- [Issues and feedback](https://github.com/pcvelz/splash-claude/issues)
- [Apache-2.0](LICENSE). GGUF kernels include MIT-licensed material from
  llama.cpp; see [third-party notices](THIRD_PARTY_NOTICES). Model weights keep their own licenses.
