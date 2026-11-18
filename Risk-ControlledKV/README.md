# Risk-Controlled KV-Cache Eviction

Code for [Risk-Controlled KV-Cache Eviction: From Memory Budgets to Risk Targets](https://arxiv.org/abs/2609.27981).

This repository provides risk calibration utilities and examples for DefensiveKV, Layer-DefensiveKV, SnapKV, and AdaKV.

## CPU calibration example

Python 3.10 or later; no GPU required:

```bash
python -m pip install -r requirements.txt
python demo.py
python -m unittest discover -s tests -v
```

The example creates independent calibration (CAL) and held-out test (TEST) samples with NumPy seed 42. It selects a policy using only CAL and then evaluates that frozen policy on TEST. All printed results are synthetic.

## Run the four compressors on a GPU

`gpu_demo.py` loads a real model, generates a paired Full-KV answer, then runs the requested compressors in separate processes. It records each answer and the actual number of retained prefill KV positions. The default model is Mistral-7B-Instruct-v0.3; the default synthetic archive context is included in the script, so no benchmark download is needed.

The evaluated backend expects Python 3.10, PyTorch 2.4.1, Transformers 4.57.0, Triton 3.0.0, and FlashAttention 2.8.3. Use a CUDA GPU with enough memory for a 7B model (the study used 48 GB A6000 GPUs). The first run downloads model weights through Hugging Face unless they are already cached. Llama models may require an authenticated account with model access.

```bash
conda create -n riskkv-gpu python=3.10 -y
conda activate riskkv-gpu
python -m pip install torch==2.4.1 --index-url https://download.pytorch.org/whl/cu124
python -m pip install -r requirements-gpu.txt
python -m pip install flash-attn==2.8.3 --no-build-isolation

# Full KV plus all four compressors, sequentially on GPU 0.
CUDA_VISIBLE_DEVICES=0 python gpu_demo.py --method all --retained-ratio 0.50
```

AdaKV, DefensiveKV, and Layer-DefensiveKV require the split-head cache CUDA extension for decoding. If `tiny_api_cuda` is already installed, the example uses it. Otherwise it builds the included source automatically on first use, using PyTorch's extension cache and the visible GPU architecture. This requires a matching CUDA toolkit (`nvcc`), C++ compiler, and Ninja; set `CUDA_HOME` to your CUDA toolkit if necessary. Building an extension is separate from installing a PyTorch CUDA wheel. Limit compilation parallelism with `MAX_JOBS=2` if needed.

Run one method and its Full-KV reference:

```bash
CUDA_VISIBLE_DEVICES=0 python gpu_demo.py --method defensivekv
CUDA_VISIBLE_DEVICES=0 python gpu_demo.py --method layerdefensivekv
CUDA_VISIBLE_DEVICES=0 python gpu_demo.py --method snapkv
CUDA_VISIBLE_DEVICES=0 python gpu_demo.py --method adakv
```

Use your own input or Llama:

```bash
CUDA_VISIBLE_DEVICES=0 python gpu_demo.py --method all \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --context-file context.txt --question "Summarize the key facts." \
  --retained-ratio 0.50 --max-new-tokens 64 --output outputs/custom
```

Input context is capped at 4096 tokens by default; change `--max-context-tokens` for longer inputs. Compression applies to the formatted context prefix, before the question and generation. Results are saved as `outputs/gpu_demo/full.json` and one JSON file per compressor, with generation text, nominal retention, actual retention, and peak allocated GPU memory. Layer-DefensiveKV still requires the full context prefill peak; retained KV fraction is not a peak-memory saving estimate.

This single-input example is a compressor smoke test, not a population-risk certificate. For risk control, obtain task-specific paired utilities on an independent CAL set and pass them to `degradation_metrics` and `fixed_sequence` as below. Generation text alone is not a task utility.

Validation: CPU calibration tests, process-command checks, cache-layout counting, and backend imports passed. No CUDA GPU is connected to the release-preparation host, so the real model generation and extension build have not been executed during this update.

## Core idea

Given paired full-cache and policy utilities in `[0, 1]`, a material-degradation event occurs when:

```python
utility_full - utility_policy > tau
```

The user specifies `tau` (loss tolerance), `epsilon` (population event-risk target), and `delta` (calibration error level). The procedure applies a lower-tail binomial-envelope test to candidates ordered from conservative to aggressive eviction. It stops at the first failed certification and returns the last certified candidate. If the first test fails, it returns the paired full-KV reference as fallback.

```python
from riskkv import Contract, degradation_metrics, fixed_sequence

contract = Contract(tau=0.10, epsilon=0.05, delta=0.05)

# Paired CAL utility arrays for a policy declared before CAL inspection.
metrics = degradation_metrics(full_utilities, policy_utilities, contract.tau)
candidates = [dict(policy="candidate", retained_ratio=0.8, split="CAL", **metrics)]
tested, selected = fixed_sequence(candidates, contract)
```

## Files

- `riskkv/certification.py`: contract, event metrics, risk test, and selection.
- `demo.py`: executable synthetic CAL/TEST example.
- `gpu_demo.py`: real Full-KV and four-compressor generation, with actual retained-cache counts.
- `third_party/defensivekv/`: attributed compressor source and split-head cache CUDA kernel.
- `requirements-gpu.txt`: optional GPU dependencies.
- `tests/test_certification.py`: finite-sample validity, stopping, fallback, and strict-threshold checks.

## Guarantee and assumptions

For a sequence and contract fixed before CAL inspection, the procedure controls the probability of returning a policy with population material-degradation risk above `epsilon` at level `delta`, assuming independent calibration requests. For equally allocated task strata, the controlled population is the uniform task mixture. The result does not provide a per-task guarantee, and does not require monotone risk or independent evaluations across candidate policies.

`fixed_sequence` accepts a completed candidate table, which may contain later candidates. Those candidates cannot affect selection after the first failed test. The `sequence_closed` field assumes the supplied list is the complete declared sequence; an inference runner evaluating only a partial prefix must separately track whether that sequence has finished.

The binomial envelope is used only in the valid lower-tail range; its p-value is 1 elsewhere. The fallback reuses the paired full-KV reference, giving zero relative degradation by definition. TEST rates describe held-out performance and do not alter CAL certification. Multiple sequence outputs or contracts do not receive a simultaneous guarantee from this minimal implementation.

## Attribution and license

The wrapper uses existing Learn-then-Test fixed-sequence testing and the Hoeffding binomial comparison; it is not a new general-purpose testing theorem or a new KV eviction algorithm. Original wrapper code is licensed under Apache-2.0; see `LICENSE`. The vendored DefensiveKV/KVPress code retains its original copyright and license notices, including MIT-licensed components; see `THIRD_PARTY_NOTICES.md`.

