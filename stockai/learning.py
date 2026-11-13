from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
import json
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, brier_score_loss,
                             f1_score, precision_score, recall_score, roc_auc_score, roc_curve)
from sklearn.preprocessing import StandardScaler

from .data import features

HORIZONS = (1, 5, 20)
MODELS = ("Random Forest", "XGBoost", "LSTM", "GRU", "Transformer")
DEEP = MODELS[2:]


@dataclass
class Settings:
    epochs: int = 20
    batch: int = 64
    lr: float = .0001
    sequence: int = 60
    device: str = "Auto"
    patience: int = 5
    period: int = 14
    trials: int = 0


def walk_forward(positions):
    n = len(positions)
    edges = np.linspace(int(n*.52), n, 4, dtype=int)
    for start, end in zip(edges[:-1], edges[1:]):
        val_start = start - max(60, n//9)
        train = np.flatnonzero(positions + max(HORIZONS) < positions[val_start])
        valid = np.flatnonzero((np.arange(n) >= val_start) &
                               (positions + max(HORIZONS) < positions[start]))
        test = np.arange(start, end)
        if min(len(train), len(valid), len(test)) < 25:
            raise ValueError("검증에 필요한 데이터가 부족합니다. 일봉을 450개 이상 확보하세요.")
        yield train, valid, test


def dataset(bars, settings):
    values = features(bars, settings.period).to_numpy(dtype=np.float32)
    prices = bars.close.to_numpy()
    positions = np.arange(settings.sequence-1, len(bars))
    windows = np.lib.stride_tricks.sliding_window_view(values, settings.sequence, axis=0).swapaxes(1, 2)
    keep = np.isfinite(windows).all(axis=(1, 2))
    positions, windows = positions[keep], np.ascontiguousarray(windows[keep])
    if not len(windows) or positions[-1] != len(bars)-1:
        raise ValueError("최신 구간의 연속 유효 특징이 부족합니다.")
    supervised = positions + max(HORIZONS) < len(prices)
    samples = positions[supervised]
    if len(samples) < 300:
        raise ValueError("학습 가능한 표본이 부족합니다. 일봉을 450개 이상 확보하세요.")
    targets = np.stack([prices[samples+h]/prices[samples]-1 for h in HORIZONS], axis=1)
    return windows[supervised], targets, samples, windows[-1:]


class Trees:
    def __init__(self, name, seed=21):
        self.pairs = []
        for _ in HORIZONS:
            if name == "Random Forest":
                kwargs = dict(n_estimators=90, max_depth=7, min_samples_leaf=5, n_jobs=2, random_state=seed)
                pair = RandomForestClassifier(**kwargs), RandomForestRegressor(**kwargs)
            else:
                from xgboost import XGBClassifier, XGBRegressor
                kwargs = dict(n_estimators=100, max_depth=3, learning_rate=.045, n_jobs=2, random_state=seed)
                pair = XGBClassifier(**kwargs), XGBRegressor(**kwargs)
            self.pairs.append(pair)

    def fit(self, x, y):
        for i, (classifier, regressor) in enumerate(self.pairs):
            labels = (y[:, i]>0).astype(int)
            if len(np.unique(labels)) < 2:
                self.pairs[i] = (float(labels.mean()), regressor)
            else:
                classifier.fit(x[:, -1], labels)
            regressor.fit(x[:, -1], y[:, i])

    def predict(self, x):
        x = x[:, -1]
        probabilities, returns = [], []
        for classifier, regressor in self.pairs:
            probabilities.append(np.full(len(x), classifier) if isinstance(classifier, float)
                                 else classifier.predict_proba(x)[:, 1])
            returns.append(regressor.predict(x))
        return np.stack(probabilities, axis=1), np.stack(returns, axis=1)


def train_fold(name, settings, x, y, fit, valid, progress, phase):
    scaler = StandardScaler().fit(x[fit].reshape(-1, x.shape[-1]))

    def scale(rows):
        return scaler.transform(rows.reshape(-1, rows.shape[-1])).reshape(rows.shape)

    train_x, val_x = scale(x[fit]), scale(x[valid])
    if name in DEEP:
        from .networks import NeuralModel
        model = NeuralModel(name, settings, x.shape[-1])
        model.fit(train_x, y[fit], val_x, y[valid], progress, phase)
    else:
        model = Trees(name)
        model.fit(train_x, y[fit])
    return model, scaler, scale


def calibrate(prob, returns, target):
    calibrators, bands = [], []
    for i in range(len(HORIZONS)):
        labels = target[:, i] > 0
        cal = None
        if len(np.unique(labels)) == 2:
            cal = LogisticRegression(C=1.0).fit(prob[:, i:i+1], labels)
        calibrators.append(cal)
        bands.append(float(np.quantile(np.abs(returns[:, i]-target[:, i]), .90)))
    return calibrators, bands


def apply_calibration(prob, calibrators):
    result = prob.copy()
    for i, cal in enumerate(calibrators):
        if cal is not None:
            result[:, i] = cal.predict_proba(prob[:, i:i+1])[:, 1]
    return result


def score(target, p, r, close):
    result = {}
    for i, h in enumerate(HORIZONS):
        truth = target[:, i] > 0
        guess = p[:, i] >= .5
        error = r[:, i]-target[:, i]
        has_classes = len(np.unique(truth)) == 2
        roc = [part.tolist() for part in roc_curve(truth, p[:, i])[:2]] if has_classes else None
        result[str(h)] = {
            "accuracy": accuracy_score(truth, guess),
            "balanced_accuracy": balanced_accuracy_score(truth, guess) if has_classes else None,
            "precision": precision_score(truth, guess, zero_division=0),
            "recall": recall_score(truth, guess, zero_division=0),
            "f1": f1_score(truth, guess, zero_division=0),
            "auc": roc_auc_score(truth, p[:, i]) if has_classes else None,
            "brier": brier_score_loss(truth, p[:, i]),
            "return_mae": np.abs(error).mean(), "return_rmse": np.sqrt(np.mean(error**2)),
            "price_mae": np.abs(error*close).mean(),
            "price_rmse": np.sqrt(np.mean((error*close)**2)),
            "directional_accuracy": accuracy_score(truth, r[:, i] > 0), "roc": roc,
        }
    return result


def atomic_json(path, payload):
    path = Path(path)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            json.dump(payload, out, ensure_ascii=False, allow_nan=False)
        except Exception:
            out.close()
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def train(bars, name, settings, destination, progress=lambda _: None):
    started = monotonic()
    x, y, positions, latest = dataset(bars, settings)
    splits = list(walk_forward(positions))
    if settings.trials and name in DEEP:
        import optuna
        from dataclasses import replace
        fit, valid, _ = splits[0]

        def objective(trial):
            candidate = replace(settings, lr=trial.suggest_float("lr", 1e-5, 2e-3, log=True),
                                batch=trial.suggest_categorical("batch", [16, 32, 64]))
            model, _, scale = train_fold(name, candidate, x, y, fit, valid, progress, "Optuna")
            p, _ = model.predict(scale(x[valid]))
            return float(np.mean((p-(y[valid]>0))**2))

        study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=21))
        study.optimize(objective, n_trials=settings.trials)
        settings = replace(settings, **study.best_params)

    predicted, returns, actual, indexes, fold_quality = [], [], [], [], []
    for phase, (fit, valid, test) in enumerate(splits, 1):
        progress({"message": f"Walk-forward {phase}/{len(splits)}", "phase": phase})
        model, scaler, scale = train_fold(name, settings, x, y, fit, valid, progress, phase)
        p_val, r_val = model.predict(scale(x[valid]))
        calibrators, bands = calibrate(p_val, r_val, y[valid])
        fold_quality.append(float(np.mean((p_val-(y[valid]>0))**2)))
        p, r = model.predict(scale(x[test]))
        predicted.extend(apply_calibration(p, calibrators).tolist())
        returns.extend(r.tolist())
        actual.extend(y[test].tolist())
        indexes.extend(positions[test].tolist())

    # Deployment fit can use later labels. Evaluation above remains frozen.
    cutoff = len(x)-max(65, len(x)//9)
    fit = np.flatnonzero(positions + max(HORIZONS) < positions[cutoff])
    valid = np.arange(cutoff, len(x))
    model, scaler, scale = train_fold(name, settings, x, y, fit, valid, progress, "Final")
    p_val, r_val = model.predict(scale(x[valid]))
    calibrators, bands = calibrate(p_val, r_val, y[valid])
    p, r = model.predict(scale(latest))
    p = apply_calibration(p, calibrators)[0]
    p, r = np.asarray(p, dtype=float), np.asarray(r[0], dtype=float)
    close = float(bars.close.iloc[-1])
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4
    artifact = uuid4().hex
    joblib.dump({"scaler": scaler, "calibrators": calibrators,
                 "model": None if name in DEEP else model}, destination / f"{artifact}.joblib")
    if name in DEEP:
        model.save(destination / f"{artifact}.safetensors")
    result = {
        "model": name, "settings": asdict(settings), "artifact": artifact,
        "seconds": monotonic()-started, "asof": bars.index[-1].isoformat(),
        "validation_brier": fold_quality, "probability": p.tolist(), "return": r.tolist(),
        "price": (close*(1+r)).tolist(), "residual_band": bands,
        "metrics": score(np.asarray(actual), np.asarray(predicted), np.asarray(returns), bars.close.iloc[indexes].to_numpy()),
        "dates": [bars.index[i].isoformat() for i in indexes], "oos_p": predicted,
        "oos_r": returns, "oos_actual": actual,
    }
    atomic_json(destination / f"{name}.json", result)
    return result


def ensemble(results, weighted=False):
    if not results:
        raise ValueError("비교할 모델을 학습하세요.")
    if any(r["dates"] != results[0]["dates"] or r["asof"] != results[0]["asof"] for r in results):
        raise ValueError("동일한 데이터와 설정으로 학습한 모델이 필요합니다.")
    # OOS uses equal weights. Fold-specific validation weight histories are reported separately.
    weight = np.array([1/max(r["validation_brier"][-1], 1e-6) if weighted else 1 for r in results])
    return {key: np.average([r[key] for r in results], weights=weight, axis=0).tolist()
            for key in ("probability", "return", "price")}
