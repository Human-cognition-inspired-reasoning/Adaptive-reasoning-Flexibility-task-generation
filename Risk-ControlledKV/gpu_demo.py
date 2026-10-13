"""Run real Full-KV and four compressor generations in isolated processes."""
import argparse
import functools
import importlib
import json
from pathlib import Path
import subprocess
import sys

METHODS = ["defensivekv", "layerdefensivekv", "snapkv", "adakv"]
ROOT = Path(__file__).resolve().parent


def default_context():
    notes = "\n".join(f"Archive entry {i}: the library records weather observations and ship arrivals." for i in range(80))
    return ("Use the archive to answer the question.\n"
            "The expedition's destination is Aurora Island and its access code is 7319.\n"
            + notes + "\nRemember: the destination is Aurora Island; the access code is 7319.")


def extension_for_adaptive_cache():
    """The evaluated split-head cache needs a CUDA kernel during decoding."""
    try:
        return importlib.import_module("tiny_api_cuda")
    except ImportError:
        from torch.utils.cpp_extension import CUDA_HOME, load
        if CUDA_HOME is None:
            raise RuntimeError("CUDA toolkit/nvcc is needed to build the split-head cache kernel; set CUDA_HOME")
        base = ROOT/"third_party/defensivekv/kvpress/csrc"
        print("Building split-head cache CUDA extension (first run only) ...", flush=True)
        module = load(
            name="riskkv_tiny_api_cuda",
            sources=[str(base/"csrc/cuda_api.cu")],
            extra_include_paths=[str(base/"csrc"), str(base/"include")],
            extra_cflags=["-O3", "-std=c++17"],
            extra_cuda_cflags=["-O3", "-std=c++17", "--use_fast_math",
                "-U__CUDA_NO_HALF_OPERATORS__", "-U__CUDA_NO_HALF_CONVERSIONS__",
                "-U__CUDA_NO_HALF2_OPERATORS__", "-U__CUDA_NO_BFLOAT16_CONVERSIONS__",
                "--expt-relaxed-constexpr", "--expt-extended-lambda"],
            with_cuda=True,
            verbose=False,
        )
        sys.modules["tiny_api_cuda"] = module
        return module


def cache_positions(cache):
    """Count retained (layer, KV-head, token) positions in both cache layouts."""
    if hasattr(cache, "metadata_list"):
        return sum(int(meta.head_lens.sum().item()) for meta in cache.metadata_list)
    return sum(int(layer.keys.numel() // layer.keys.shape[-1]) for layer in cache.layers)


def worker(args):
    import torch
    from importlib.metadata import version
    if not torch.cuda.is_available():
        raise RuntimeError("No CUDA GPU is visible. Use demo.py for the CPU certification example.")
    if version("transformers") != "4.57.0":
        raise RuntimeError("This backend expects transformers==4.57.0; see requirements-gpu.txt")
    adaptive = args.method in {"defensivekv", "layerdefensivekv", "adakv"}
    if adaptive:
        extension_for_adaptive_cache()
    sys.path.insert(0, str(ROOT/"third_party/defensivekv"))
    import kvpress
    from kvpress import EfficientDefensiveKVPress, EfficientLayerDefensiveKVPress, SnapKVPress, EfficientAdaSnapKVPress
    from kvpress.ada_attn import replace_var_flash_attn
    from kvpress.ada_cache import DynamicCacheSplitHeadFlatten
    from transformers import DynamicCache, pipeline

    methods = {"defensivekv": EfficientDefensiveKVPress, "layerdefensivekv": EfficientLayerDefensiveKVPress,
               "snapkv": SnapKVPress, "adakv": EfficientAdaSnapKVPress}
    kwargs = {"torch_dtype": torch.bfloat16}
    if adaptive:
        replace_var_flash_attn(model_name=args.model)
    else:
        kwargs["attn_implementation"] = "flash_attention_2"
    pipe = pipeline("kv-press-text-generation", model=args.model, device=args.device, model_kwargs=kwargs)
    pipe.model.eval()
    forward = pipe.model.forward
    @functools.wraps(forward)
    def one_position_logits(*positional, **named):
        if "num_logits_to_keep" in named:
            named["logits_to_keep"] = named.pop("num_logits_to_keep")
        return forward(*positional, **named)
    pipe.model.forward = one_position_logits

    press = None if args.method == "full" else methods[args.method]()
    if press is not None:
        press.compression_ratio = 1.0 - args.retained_ratio
    cache = DynamicCacheSplitHeadFlatten() if adaptive else DynamicCache()
    context = args.context_file.read_text() if args.context_file else default_context()
    inputs = pipe.preprocess(context, questions=[args.question], answer_prefix="",
                             max_context_length=args.max_context_tokens)
    length = int(inputs["context_ids"].shape[-1])
    # The scored-window mechanisms need an adequate compressible prefix.
    if length <= 32 or (press is not None and int(length*args.retained_ratio) <= 32):
        raise ValueError("Input/retention budget is too short for the 32-token observation window")
    torch.cuda.reset_peak_memory_stats(args.device)
    with torch.inference_mode():
        answer = pipe._forward(inputs, press=press, cache=cache, max_new_tokens=args.max_new_tokens)[0]
    kept = cache_positions(cache)
    heads = int(pipe.model.config.num_key_value_heads)
    layers = int(pipe.model.config.num_hidden_layers)
    full_positions = length * heads * layers
    result = dict(method=args.method, model=args.model, context_tokens=length,
                  nominal_retained_ratio=1.0 if press is None else args.retained_ratio,
                  retained_kv_positions=kept, full_kv_positions=full_positions,
                  realized_retained_ratio=kept/full_positions,
                  peak_gpu_allocated_gib=torch.cuda.max_memory_allocated(args.device)/2**30,
                  question=args.question, answer=answer)
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output/f"{args.method}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n")
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)


def commands(args):
    requested = METHODS if args.method == "all" else [args.method]
    requested = ["full"] + requested if args.method != "full" else ["full"]
    output = []
    for method in requested:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--method", method,
                   "--model", args.model, "--device", args.device,
                   "--retained-ratio", str(args.retained_ratio), "--question", args.question,
                   "--max-new-tokens", str(args.max_new_tokens), "--max-context-tokens", str(args.max_context_tokens),
                   "--output", str(args.output.resolve())]
        if args.context_file:
            command += ["--context-file", str(args.context_file.resolve())]
        output.append(command)
    return output


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--method", choices=["all", "full"]+METHODS, default="all")
    ap.add_argument("--model", default="mistralai/Mistral-7B-Instruct-v0.3")
    ap.add_argument("--retained-ratio", type=float, default=.50)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--context-file", type=Path)
    ap.add_argument("--question", default="What is the expedition's destination and access code? Answer briefly.")
    ap.add_argument("--max-new-tokens", type=int, default=32)
    ap.add_argument("--max-context-tokens", type=int, default=4096)
    ap.add_argument("--output", type=Path, default=Path("outputs/gpu_demo"))
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if not 0 < args.retained_ratio < 1 or args.max_new_tokens < 1 or args.max_context_tokens <= 32:
        ap.error("Use 0 < retention < 1, positive generation length, and context cap > 32")
    if args.context_file and not args.context_file.is_file():
        ap.error(f"Context file not found: {args.context_file}")
    if args.worker:
        worker(args)
    else:
        for command in commands(args):
            print(f"Running {command[command.index('--method')+1]} in a clean process ...", flush=True)
            subprocess.run(command, check=True)
        print(f"Completed. Generation results and actual KV counts: {args.output}")
        print("This is a generation smoke test, not a population-risk certificate.")


if __name__ == "__main__":
    main()

