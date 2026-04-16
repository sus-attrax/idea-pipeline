"""Scoring engine: additive, weighted scoring across idea/chance/wissen.

TODO Step 5: Implement T0 scoring (vault-only, no research).
Formula (v1, weights from config/weights.yaml):
    idea_total = w1*chance_avg + w2*wissen_avg + w3*intrinsic_avg

All values inverted (7 - x) so larger = better.
"""
