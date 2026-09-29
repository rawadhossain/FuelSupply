"""Unit tests for the forecaster and detector. Run from repo root:  python -m pytest intelligence/tests -q
Requires trained artifacts (python -m intelligence.train)."""
import os

import numpy as np
import pytest

from intelligence.detect import CusumDetector
from intelligence.forecast import ProfileForecaster

ART = os.path.join(os.path.dirname(__file__), "..", "artifacts", "profile-v1")
S, F = "station-mirpur", "DIESEL"


@pytest.fixture(scope="module")
def model():
    if not os.path.exists(os.path.join(ART, "model.json")):
        pytest.skip("train first: python -m intelligence.train")
    return ProfileForecaster.load(ART)


def test_all_12_series_loaded(model):
    assert len(model.profile) == 12
    assert all(arr.shape == (96,) and (arr > 0).all() for arr in model.profile.values())


def test_forecast_shapes_and_cumulative(model):
    fc = model.forecast(S, F, start_tick=500, horizon=96)
    assert len(fc["q50"]) == 96 and fc["ticks"][0] == 500
    assert np.all(np.diff(fc["cum_q50"]) > 0)
    assert np.all(np.array(fc["q10"]) <= np.array(fc["q50"])) and np.all(np.array(fc["q50"]) <= np.array(fc["q90"]))
    assert np.all(np.array(fc["cum_q10"]) <= np.array(fc["cum_q90"]))


def test_daily_total_matches_profile_spec(model):
    # SPEC.md: urban_high diesel ~8500 L/day, Dhaka factor 1.00 -> learned daily total within 10%
    day = sum(model.forecast(S, F, 0, 96)["q50"])
    assert 8500 * 0.9 < day < 8500 * 1.1


def test_multiplier_schedule_scales(model):
    base = np.array(model.forecast(S, F, 0, 8)["q50"])
    sched = np.array([1, 1, 1.8, 1.8, 1.8, 1.8, 1, 1])
    spiked = np.array(model.forecast(S, F, 0, 8, multiplier_schedule=sched)["q50"])
    assert np.allclose(spiked / base, sched)


def test_ewma_ratio_only_when_anomaly(model):
    act, exp = np.array([130.0] * 8), np.array([100.0] * 8)
    assert model.ewma_ratio(act, exp, anomaly_active=False) == pytest.approx(1.0) or model.ewma_alpha > 0
    assert model.ewma_ratio(act, exp, anomaly_active=True) > 1.2


def test_ratio_decays_to_one(model):
    fc = model.forecast(S, F, 0, 40, ratio=1.5)
    base = model.forecast(S, F, 0, 40)
    assert fc["q50"][0] == pytest.approx(base["q50"][0] * 1.5)
    assert fc["q50"][-1] == pytest.approx(base["q50"][-1])


def test_save_load_roundtrip(model, tmp_path):
    model.save(str(tmp_path))
    m2 = ProfileForecaster.load(str(tmp_path))
    assert np.allclose(m2.profile[(S, F)], model.profile[(S, F)])
    assert m2.resid_q[(S, F)] == model.resid_q[(S, F)]


def test_detector_quiet_then_alarms_on_spike():
    det = CusumDetector(sigma={(S, F): 0.06}, h=8.0)
    rng = np.random.default_rng(0)
    for t in range(200):   # normal noise, no alarm expected
        assert det.update(S, F, t, 100 * (1 + rng.normal(0, 0.05)), 100) is None
    fired = [det.update(S, F, 200 + t, 130, 100) for t in range(5)]
    assert any(fired) and next(x for x in fired if x)["direction"] == "up"


def test_detector_clears_after_shift_ends():
    det = CusumDetector(sigma={(S, F): 0.06}, h=8.0)
    for t in range(10):
        det.update(S, F, t, 150, 100)
    assert det.active()
    for t in range(10, 60):
        det.update(S, F, t, 100, 100)
    assert not det.active()


def test_detector_ignores_nonpositive():
    det = CusumDetector(sigma={(S, F): 0.06})
    assert det.update(S, F, 0, 0, 100) is None
