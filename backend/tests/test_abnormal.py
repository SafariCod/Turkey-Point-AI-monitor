from status_engine import FeatureVector, ai_interpretation


def test_abnormal_classification():
    feats = FeatureVector(z_scores={"voc": -2.7}, trend_slopes={"voc": 0.0}, abnormal_count=1)
    interp = ai_interpretation(feats)
    assert interp.status == "ABNORMAL"
