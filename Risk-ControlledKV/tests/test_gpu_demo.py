import argparse
from pathlib import Path
import unittest

from gpu_demo import METHODS, cache_positions, commands


class FakeTensor:
    def __init__(self, shape): self.shape = shape
    def numel(self):
        value = 1
        for dim in self.shape: value *= dim
        return value


class FakeLengths:
    def __init__(self, lengths): self.lengths = lengths
    def sum(self): return self
    def item(self): return sum(self.lengths)


class DemoTests(unittest.TestCase):
    def test_all_methods_use_separate_worker_commands(self):
        args = argparse.Namespace(method="all", model="model", device="cuda:0", retained_ratio=.5,
            question="question", max_new_tokens=32, max_context_tokens=4096,
            output=Path("outputs/example"), context_file=None)
        cmds = commands(args)
        self.assertEqual([cmd[cmd.index("--method")+1] for cmd in cmds], ["full"]+METHODS)
        self.assertTrue(all("--worker" in cmd for cmd in cmds))

    def test_actual_position_count_supports_both_cache_layouts(self):
        cache = argparse.Namespace(layers=[argparse.Namespace(keys=FakeTensor((1, 8, 200, 128))) for _ in range(2)])
        self.assertEqual(cache_positions(cache), 3200)
        flattened = argparse.Namespace(metadata_list=[argparse.Namespace(head_lens=FakeLengths([100, 80])),
                                                       argparse.Namespace(head_lens=FakeLengths([120, 60]))])
        self.assertEqual(cache_positions(flattened), 360)


if __name__ == "__main__":
    unittest.main()
