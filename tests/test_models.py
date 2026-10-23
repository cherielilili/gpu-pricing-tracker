"""Invariants for the GPU model catalog.

Guards against the two bug classes this repo has actually hit:
  1. A fetcher emitting a gpu_model that report.py can't display — Vast's
     "A100X" once emitted the non-canonical "A100_80GB", which silently
     vanishes from every table because they iterate GPU_ORDER.
  2. Display metadata (GPU_LABEL / GPU_NOTE) drifting out of sync with the
     canonical GPU_ORDER list.

Run: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import report  # noqa: E402
import runpod_fetcher  # noqa: E402
import vast_fetcher  # noqa: E402
from crusoe_fetcher import _normalize, parse  # noqa: E402

FETCHERS = (runpod_fetcher, vast_fetcher)

RTX6000_IDS = {
    "RTX_PRO_6000",
    "RTX_PRO_6000_MAXQ",
    "RTX_PRO_6000_WK",
    "RTX_6000_ADA",
    "RTX_A6000",
}


class CatalogTest(unittest.TestCase):
    def test_gpu_label_covers_order_exactly(self):
        self.assertEqual(set(report.GPU_ORDER), set(report.GPU_LABEL))

    def test_note_keys_are_canonical(self):
        self.assertLessEqual(set(report.GPU_NOTE), set(report.GPU_ORDER))

    def test_fetchers_emit_only_canonical_models(self):
        for mod in FETCHERS:
            for raw, canonical in mod.GPU_MAP.items():
                self.assertIn(
                    canonical,
                    report.GPU_ORDER,
                    f"{mod.__name__} maps {raw!r} to non-canonical {canonical!r}",
                )


class Rtx6000SplitTest(unittest.TestCase):
    """The RTX 6000 family spans ~5x in price on the same provider (RunPod:
    MaxQ $0.50 ... WK $2.59). One bucket would make medians track the listing
    mix instead of price, so every edition/generation is its own model.
    """

    def test_family_maps_to_five_distinct_ids(self):
        for mod in FETCHERS:
            vals = {v for k, v in mod.GPU_MAP.items() if "6000" in k.upper()}
            self.assertEqual(vals, RTX6000_IDS, msg=mod.__name__)

    def test_mig_slices_and_turing_quadro_excluded(self):
        for mod in FETCHERS:
            for raw in mod.GPU_MAP:
                self.assertNotIn("MIG", raw.upper(), f"{mod.__name__} tracks MIG slice {raw!r}")
                self.assertNotIn("Q RTX", raw.upper(), f"{mod.__name__} tracks legacy {raw!r}")


# Minimal shape of the real Crusoe pricing page: GPU-table region holds the
# cards, then a flat cell block (whole On-demand column, then Current-spot).
# Regression locks: section scoping, tags-with-embedded-HTML prices, and
# positional card/cell pairing.
CRUSOE_FIXTURE = """
<div class="pricing-heading is-d">GPU model</div>
<div data-sort-name="NVIDIA B300" ...attrs...><h4 class="pricing-item-heading">NVIDIA B300</h4>
  <div class="pricing_tags-wr"><div class="pricing-tag is-dark">288GB</div><div class="pricing-tag">HGX</div></div></a></div>
<div data-sort-name="NVIDIA A100" ...attrs...><h4>NVIDIA A100</h4>
  <div class="pricing_tags-wr"><div class="pricing-tag is-dark">80GB</div><div class="pricing-tag">PCIe</div></div></a></div>
<div role="listitem" class="gpu-row w-dyn-item"><div class="pricing-rich is-dp is-sl"><p>$8.96</p><p>/</p><p>GPU-hr</p></div></div>
<div role="listitem" class="gpu-row w-dyn-item"><div class="pricing-rich is-dp is-sl"><p>$2.00</p><p>/</p><p>GPU-hr</p></div></div>
<div role="listitem" class="gpu-row w-dyn-item"><div class="pricing-rich is-dp is-sl"><p>$7.00</p><p>/</p><p>GPU-hr</p></div></div>
<div role="listitem" class="gpu-row w-dyn-item"><div class="pricing-rich w-richtext"><p>Contact sales</p></div></div>
<div class="pricing-heading is-d">CPU Type</div>
<div data-sort-name="General-purpose"><div class="pricing-rich is-dp is-sl"><p>$99.99</p><p>/</p><p>GPU-hr</p></div></div>
"""

# Same page with the trailing spot cell absent (2n-1 cells): the spot
# column omits the last card, whose on-demand price must survive.
CRUSOE_FIXTURE_SHORT_SPOT = CRUSOE_FIXTURE.replace(
    """
<div role="listitem" class="gpu-row w-dyn-item"><div class="pricing-rich w-richtext"><p>Contact sales</p></div></div>
<div class="pricing-heading is-d">CPU Type</div>""",
    """
<div class="pricing-heading is-d">CPU Type</div>""",
)


class CrusoeParserTest(unittest.TestCase):
    def test_parses_on_demand_and_spot_columns(self):
        got = parse(CRUSOE_FIXTURE)
        self.assertEqual(
            got, [("B300", 8.96, 7.0), ("A100_PCIE_80GB", 2.0, None)]
        )

    def test_short_spot_column_keeps_last_card_on_demand(self):
        got = parse(CRUSOE_FIXTURE_SHORT_SPOT)
        self.assertEqual(
            got, [("B300", 8.96, 7.0), ("A100_PCIE_80GB", 2.0, None)]
        )

    def test_layout_drift_returns_none(self):
        # cards without a matching cell block must never mis-pair
        broken = CRUSOE_FIXTURE.replace('class="gpu-row w-dyn-item"', 'class="other-row"')
        self.assertIsNone(parse(broken))

    def test_normalize_outputs_are_canonical(self):
        cases = [
            ((("NVIDIA", "B300"), ("288GB", "HGX")), "B300"),
            ((("NVIDIA", "H100"), ("PCIe",)), "H100_PCIE"),
            ((("NVIDIA", "H100"), ("HGX",)), "H100_SXM"),
            ((("NVIDIA", "MI300X"), ()), None),  # wrong vendor — None path
            ((("AMD", "MI300X"), ("192GB",)), "MI300X"),
            ((("NVIDIA", "Vera Rubin"), ("288GB", "NVL72")), None),
        ]
        for (vendor_gpu, tags), want in cases:
            model = _normalize(*vendor_gpu, list(tags))
            self.assertEqual(model, want, f"{vendor_gpu} {tags}")
            if model is not None:
                self.assertIn(model, report.GPU_ORDER)


if __name__ == "__main__":
    unittest.main()
