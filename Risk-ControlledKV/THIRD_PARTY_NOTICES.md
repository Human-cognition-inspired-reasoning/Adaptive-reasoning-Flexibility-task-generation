# Third-party attribution

The GPU generation example uses the evaluated source snapshot of [DefensiveKV](https://github.com/FFY0/DefensiveKV), including its KVPress implementation. Recorded checkout HEAD: `9251b95e7e282a6c18dd98ef6318dd489e639a58`. The files come from the working snapshot; local changes relative to HEAD may be present.

Source is kept under `third_party/defensivekv/`. Its Apache-2.0 license and source-specific copyright headers are retained. The CUDA extension's MIT license is retained at `third_party/defensivekv/kvpress/csrc/LICENSE`, and the split-head cache has its original MIT header. This release does not claim ownership of the underlying eviction algorithms.

The example loads full KV, SnapKV, AdaKV (EfficientAdaSnapKVPress), DefensiveKV, and Layer-DefensiveKV without changing their scoring or allocation mechanisms. The wrapper translates the old `num_logits_to_keep` argument into Transformers 4.57.0's `logits_to_keep` argument. The split-head cache extension is automatically built from the included CUDA source if an installed `tiny_api_cuda` is unavailable. No model weights or benchmark corpora are distributed.
