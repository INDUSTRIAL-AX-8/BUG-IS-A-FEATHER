"""요인 분석(driver analysis) — 순수 함수. 데이터 구조가 달라도 같은 절차로 '왜 불량이 늘었나'를 판단한다.

입력 한 행 = 한 단위 구간(1시간 기록, 공정 run 등): 생산 수 n, 불량 수 k, 연속 요인(조건·측정값),
범주 요인(작업자 등), 최다 불량 유형(선택). 그리고 설비 이벤트(정지·정비)와 데이터 주의사항.

판단 절차 (모두 결과에 근거로 남는다)
  1. 단변량: 요인마다 준이항(quasi-binomial) 로지스틱 회귀 — 1σ 당 오즈비, p-value(과산포 보정), 설명력
  2. 다변량 전진 선택: AIC 가 좋아지고 보정 p < α 인 요인만, 최대 N개. 서로 거의 같은 요인(|r|≥기준)은 분리 불가로 표시
  3. 교란 판정: 단변량으로는 유의하지만 주요인을 넣으면 효과가 사라지는 요인 → '주요인으로 설명됨'
     범주 요인은 '주요인 값이 비슷한 행끼리' 수준별 불량률도 비교 (같은 조건에서 차이가 있나)
  4. 임계값: 주요인을 둘로 나눠 불량률 차이가 가장 큰 경계 → 학습 범위(좋은 쪽 경계)
  5. 시간 순서: 주요인의 변화 시작 시점 vs 범주 요인(예: 작업자 교체)의 시작 시점, 설비 이벤트 전후 비교
  6. 불량 유형 연관: 최다 유형이 X 인 행에서 유난히 다른 요인 (약한 근거로 표시)
  7. 결론·확신도·권고·주의사항 자동 작성

관측 데이터의 상관이므로 '원인 후보'와 '확인할 것'으로 표현하고 단정하지 않는다.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

OK, INSUFFICIENT = "OK", "INSUFFICIENT"


@dataclass
class Row:
    time: datetime
    n: float
    k: float
    numeric: dict[str, float | None]
    categorical: dict[str, str | None] = field(default_factory=dict)
    top_type: str | None = None
    label: str = ""


@dataclass
class Event:
    start: datetime
    end: datetime | None
    kind: str
    note: str | None = None


# ------------------------------------------------------------------ 통계 도구

def _fit(k: np.ndarray, n: np.ndarray, X: np.ndarray | None) -> dict | None:
    import statsmodels.api as sm

    endog = np.column_stack([k, n - k])
    exog = np.ones((len(k), 1)) if X is None or X.shape[1] == 0 else sm.add_constant(X, has_constant="add")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            base = sm.GLM(endog, exog, family=sm.families.Binomial()).fit()
            quasi = sm.GLM(endog, exog, family=sm.families.Binomial()).fit(scale="X2")
    except Exception:
        return None
    if not np.all(np.isfinite(base.params)):
        return None
    return {"params": np.asarray(quasi.params), "bse": np.asarray(quasi.bse), "p": np.asarray(quasi.pvalues),
            "aic": float(base.aic), "deviance": float(base.deviance), "null_deviance": float(base.null_deviance),
            "scale": float(quasi.scale)}


def _pseudo_r2(fit: dict) -> float:
    return 0.0 if fit["null_deviance"] <= 0 else max(0.0, 1 - fit["deviance"] / fit["null_deviance"])


@dataclass
class Factor:
    name: str
    kind: str  # numeric | categorical
    value_kind: str | None  # measured | setpoint | None
    columns: list[np.ndarray]  # 설계행렬 열 (numeric 은 표준화 1열, categorical 은 더미들)
    labels: list[str]  # 열 이름 (categorical: 수준명)
    mean: float = 0.0
    sd: float = 1.0
    reference: str | None = None


def _resolution(values: np.ndarray) -> float:
    u = np.unique(values[np.isfinite(values)])
    if len(u) < 2:
        return 1.0
    return float(np.min(np.diff(u)))


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(abs(np.corrcoef(a, b)[0, 1]))


def _fmt(v: float | None, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "-"
    return f"{v:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{v:.0f}"


def _rate(k: float, n: float) -> float | None:
    return k / n if n else None


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def _when(t: datetime) -> str:
    return t.strftime("%m-%d %H:%M")


# ------------------------------------------------------------------ 본체

def analyze(
    rows: list[Row],
    *,
    value_kinds: dict[str, str | None] | None = None,
    units: dict[str, str | None] | None = None,
    events: list[Event] | None = None,
    top_type_semantics: bool = False,
    caveats: list[str] | None = None,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    value_kinds = value_kinds or {}
    units = units or {}
    events = events or []
    rows = sorted([r for r in rows if r.n and r.n > 0], key=lambda r: r.time)
    base_caveats = list(caveats or [])
    if len(rows) < cfg["min_rows"]:
        return {"status": INSUFFICIENT, "message": f"분석 행이 {len(rows)}개입니다. 최소 {cfg['min_rows']}개가 필요합니다.",
                "verdict": {"headline": "데이터가 부족해 판단하지 않습니다.", "confidence": "없음", "reasons": [],
                            "recommendations": []}, "caveats": base_caveats, "factors": [], "rows": []}

    n = np.array([r.n for r in rows], dtype=float)
    k = np.array([min(r.k, r.n) for r in rows], dtype=float)
    total_rate = k.sum() / n.sum()

    # ---- 요인 구성 (행 전체에 값이 있는 요인만; 결측 행이 많으면 제외)
    factors: list[Factor] = []
    num_names = sorted({name for r in rows for name in r.numeric})
    for name in num_names:
        vals = np.array([np.nan if r.numeric.get(name) is None else float(r.numeric[name]) for r in rows])
        if np.isnan(vals).mean() > 0.2:
            continue
        vals = np.where(np.isnan(vals), np.nanmean(vals), vals)
        sd = float(np.std(vals))
        if sd == 0:
            continue
        factors.append(Factor(name, "numeric", value_kinds.get(name), [(vals - vals.mean()) / sd], [name],
                              float(vals.mean()), sd))
    cat_names = sorted({name for r in rows for name in r.categorical})
    for name in cat_names:
        vals = [r.categorical.get(name) or "(없음)" for r in rows]
        levels = sorted(set(vals), key=lambda v: (-vals.count(v), v))
        if len(levels) < 2:
            continue
        ref = levels[0]
        cols = [np.array([1.0 if v == lv else 0.0 for v in vals]) for lv in levels[1:]]
        factors.append(Factor(name, "categorical", None, cols, levels[1:], reference=ref))

    raw = {f.name: np.array([r.numeric.get(f.name) if f.kind == "numeric" else 0.0 for r in rows], dtype=float)
           for f in factors}

    def design(fs: list[Factor]) -> np.ndarray:
        cols = [c for f in fs for c in f.columns]
        return np.column_stack(cols) if cols else np.zeros((len(rows), 0))

    null_fit = _fit(k, n, None)
    scale0 = null_fit["scale"] if null_fit else 1.0

    # ---- 1. 단변량
    uni: dict[str, dict] = {}
    for f in factors:
        fit = _fit(k, n, design([f]))
        if fit is None:
            continue
        info = {"coef": float(fit["params"][1]), "p": float(np.min(fit["p"][1:])), "r2": _pseudo_r2(fit), "aic": fit["aic"]}
        if f.kind == "numeric":
            info["odds_ratio_per_sd"] = float(math.exp(fit["params"][1]))
        else:
            vals = [r.categorical.get(f.name) or "(없음)" for r in rows]
            info["levels"] = {lv: {"rows": sum(1 for v in vals if v == lv),
                                   "rate": _rate(sum(k[i] for i, v in enumerate(vals) if v == lv),
                                                 sum(n[i] for i, v in enumerate(vals) if v == lv))}
                              for lv in [f.reference, *f.labels]}
        uni[f.name] = info

    # ---- 2. 전진 선택
    selected: list[Factor] = []
    inseparable: dict[str, tuple[str, float]] = {}
    matched_out: dict[str, dict] = {}  # 같은 조건 비교에서 차이가 없어 제외된 요인
    expected_primary: np.ndarray | None = None
    current = null_fit
    alpha = cfg["alpha"]
    while len(selected) < cfg["max_model_terms"]:
        best = None
        for f in factors:
            if f in selected or f.name in inseparable or f.name in matched_out or f.name not in uni:
                continue
            r_max, with_ = 0.0, None
            for s in selected:
                for a in f.columns:
                    for b in s.columns:
                        r = _corr(a, b)
                        if r > r_max:
                            r_max, with_ = r, s.name
            if r_max >= cfg["collinearity_r"]:
                inseparable[f.name] = (with_, r_max)
                continue
            if selected and selected[0].kind == "numeric":
                lv = _levels(f, rows, raw, cfg["discrete_levels"])
                if lv is not None:
                    mc = matched_comparison(lv, raw[selected[0].name], k, n,
                                            cfg["similar_tolerance_sigma"] * selected[0].sd, expected_primary)
                    if mc and mc["comparable"] and mc["max_rel_diff"] is not None and mc["max_rel_diff"] <= cfg["matched_rel_diff"]:
                        matched_out[f.name] = mc
                        continue
            fit = _fit(k, n, design(selected + [f]))
            if fit is None:
                continue
            idx = slice(1 + sum(len(s.columns) for s in selected), None)
            p = float(np.min(fit["p"][idx]))
            ref_dev = current["deviance"] if current else null_fit["deviance"]
            gain = (ref_dev - fit["deviance"]) / null_fit["null_deviance"] if null_fit and null_fit["null_deviance"] > 0 else 0
            if (p < alpha and gain >= cfg["min_deviance_gain"]
                    and (current is None or fit["aic"] < current["aic"] - 2)
                    and (best is None or fit["aic"] < best[1]["aic"])):
                best = (f, fit, p)
        if best is None:
            break
        selected.append(best[0])
        current = best[1]
        if len(selected) == 1:
            lin = current["params"][0] + sum(current["params"][1 + j] * c for j, c in enumerate(best[0].columns))
            expected_primary = n / (1 + np.exp(-lin))

    final = current if selected else null_fit
    model_terms = []
    if selected and final:
        offset = 1
        for f in selected:
            m = len(f.columns)
            for j in range(m):
                model_terms.append({"factor": f.name, "level": f.labels[j] if f.kind == "categorical" else None,
                                    "coef": float(final["params"][offset + j]), "p": float(final["p"][offset + j]),
                                    "odds_ratio": float(math.exp(final["params"][offset + j]))})
            offset += m

    # ---- 3. 교란 판정
    primary = selected[0] if selected else None
    assessments: dict[str, dict] = {}
    for f in factors:
        if f.name not in uni:
            continue
        u = uni[f.name]
        if f in selected:
            assessments[f.name] = {"status": "PRIMARY" if f is primary else "SECONDARY"}
            continue
        if f.name in matched_out:
            assessments[f.name] = {"status": "EXPLAINED", "by": [primary.name], "matched": True,
                                   "similar_condition": {"factor": primary.name, **matched_out[f.name]},
                                   "adjusted_p": None, "shrink": None}
            continue
        if f.name in inseparable:
            w, r = inseparable[f.name]
            assessments[f.name] = {"status": "INSEPARABLE", "with": w, "r": r}
            continue
        if u["p"] >= alpha:
            assessments[f.name] = {"status": "NOT_SIGNIFICANT"}
            continue
        fit = _fit(k, n, design(selected + [f])) if selected else None
        if fit is None:
            assessments[f.name] = {"status": "SIGNIFICANT_UNADJUSTED"}
            continue
        idx = 1 + sum(len(s.columns) for s in selected)
        adj = float(fit["params"][idx])
        adj_p = float(fit["p"][idx])
        shrink = 1 - abs(adj) / abs(u["coef"]) if u["coef"] else 0.0
        status = "EXPLAINED" if adj_p >= alpha and (shrink >= cfg["explained_shrink"] or adj * u["coef"] <= 0) else "INDEPENDENT"
        assessments[f.name] = {"status": status, "adjusted_coef": adj, "adjusted_p": adj_p, "shrink": shrink,
                               "by": [s.name for s in selected]}

    # 남은 범주/이산 요인도 같은 조건 비교 결과를 붙인다 (설명용)
    if primary is not None and primary.kind == "numeric":
        for f in factors:
            a = assessments.get(f.name)
            if a is None or f is primary or "similar_condition" in a:
                continue
            lv = _levels(f, rows, raw, cfg["discrete_levels"])
            if lv is not None:
                mc = matched_comparison(lv, raw[primary.name], k, n, cfg["similar_tolerance_sigma"] * primary.sd,
                                        expected_primary)
                if mc and mc["comparable"]:
                    a["similar_condition"] = {"factor": primary.name, **mc}

    # ---- 4. 임계값과 학습 범위 (선택된 연속 요인 모두, 주요인이 첫 번째)
    thresholds = []
    for f in selected:
        if f.kind != "numeric":
            continue
        t = next(t for t in model_terms if t["factor"] == f.name)
        th = _threshold(f.name, raw[f.name], k, n, 1 if t["coef"] > 0 else -1,
                        None if f is primary else expected_primary)
        if th:
            thresholds.append(th)
    threshold = thresholds[0] if thresholds and primary is not None and thresholds[0]["factor"] == primary.name else None

    # ---- 5. 시간 순서: 변화 시작, 범주 요인 등장, 설비 이벤트 전후
    timeline: dict[str, Any] = {}
    if primary is not None and primary.kind == "numeric" and threshold:
        pv = raw[primary.name]
        B = min(cfg["onset_baseline_rows"], len(pv) - 1)
        base_mean = float(pv[:B].mean())
        base_sd = max(float(pv[:B].std()), _resolution(pv))
        bad_high = threshold["direction"] == "high_is_bad"
        lim = base_mean + cfg["onset_sigma"] * base_sd * (1 if bad_high else -1)
        onset = next((i for i in range(B, len(pv)) if (pv[i] > lim if bad_high else pv[i] < lim)), None)
        timeline["onset"] = None if onset is None else {
            "time": rows[onset].time, "value": float(pv[onset]), "baseline_mean": base_mean, "limit": lim,
            "categorical": {c: rows[onset].categorical.get(c) for c in cat_names}}
        bad_zone = [(pv[i] > threshold["good_side_limit"]) if bad_high else (pv[i] < threshold["good_side_limit"])
                    for i in range(len(pv))]
        recovery = None
        for i in range(1, len(pv)):
            if bad_zone[i - 1] and not bad_zone[i]:
                recovery = i
        timeline["recovery"] = None if recovery is None else {"time": rows[recovery].time, "value": float(pv[recovery])}
        timeline["bad_zone"] = bad_zone
    first_seen: dict[str, dict] = {}
    for f in factors:
        if f.kind != "categorical":
            continue
        for i, r in enumerate(rows):
            v = r.categorical.get(f.name) or "(없음)"
            if v != f.reference:
                first_seen[f.name] = {"level": v, "time": r.time}
                break
    timeline["categorical_first_seen"] = first_seen

    W = cfg["event_window_rows"]
    ev_out = []
    for e in events:
        before = [i for i, r in enumerate(rows) if r.time < e.start][-W:]
        after = [i for i, r in enumerate(rows) if r.time >= (e.end or e.start)][:W]
        if not before or not after:
            continue
        item = {"kind": e.kind, "start": e.start, "end": e.end, "note": e.note,
                "rate_before": _rate(k[before].sum(), n[before].sum()), "rate_after": _rate(k[after].sum(), n[after].sum())}
        if primary is not None and primary.kind == "numeric":
            pv = raw[primary.name]
            item["primary_before"] = float(pv[before].mean())
            item["primary_after"] = float(pv[after].mean())
        ev_out.append(item)
    timeline["events"] = ev_out

    # ---- 6. 불량 유형 연관 (최다 유형만 기록된 경우 약한 근거)
    type_assoc = []
    types = [r.top_type for r in rows]
    for t in sorted({x for x in types if x}):
        idx = [i for i, x in enumerate(types) if x == t]
        rest = [i for i, x in enumerate(types) if x != t]
        if len(idx) < cfg["min_type_rows"] or len(rest) < 2:
            continue
        best = None
        for f in factors:
            if f.kind != "numeric":
                continue
            v = raw[f.name]
            d = (v[idx].mean() - v[rest].mean()) / (f.sd or 1)
            p = _mw_p(v[idx], v[rest])
            if best is None or abs(d) > abs(best["effect_sd"]):
                best = {"type": t, "rows": len(idx), "factor": f.name, "effect_sd": float(d), "p": p,
                        "mean_in": float(v[idx].mean()), "mean_out": float(v[rest].mean())}
        if best and abs(best["effect_sd"]) >= 1.0 and best["p"] is not None and best["p"] < 0.1:
            type_assoc.append(best)

    # ---- 7. 결론
    auto_caveats = [
        f"분석 행 {len(rows)}개의 관측 데이터입니다. 상관 관계이며 인과를 확정하지 않습니다.",
    ]
    if scale0 > 1.5:
        auto_caveats.append(f"구간 간 불량 변동이 이항분포보다 큽니다(과산포 {scale0:.1f}배). p-value 는 이를 보정했습니다.")
    if top_type_semantics:
        auto_caveats.append("불량 유형은 구간마다 가장 많았던 유형 하나만 있어, 유형별 연관은 약한 근거입니다.")
    verdict = _verdict(rows, n, k, total_rate, primary, selected, model_terms, final, uni, assessments, threshold,
                       timeline, type_assoc, units, value_kinds, cfg)
    factor_out = []
    for f in factors:
        if f.name not in uni:
            continue
        factor_out.append({"name": f.name, "kind": f.kind, "value_kind": f.value_kind, "unit": units.get(f.name),
                           **uni[f.name], **assessments.get(f.name, {}),
                           **({"mean": f.mean, "sd": f.sd} if f.kind == "numeric" else {"reference": f.reference})})
    rank = {"PRIMARY": 0, "SECONDARY": 1, "INDEPENDENT": 2, "INSEPARABLE": 3, "EXPLAINED": 4,
            "SIGNIFICANT_UNADJUSTED": 5, "NOT_SIGNIFICANT": 6}
    factor_out.sort(key=lambda x: (rank.get(x.get("status", ""), 9), x["p"]))
    return {
        "status": OK,
        "summary": {"rows": len(rows), "produced": float(n.sum()), "defects": float(k.sum()), "rate": total_rate,
                    "from": rows[0].time, "to": rows[-1].time, "overdispersion": scale0},
        "model": {"terms": model_terms, "pseudo_r2": _pseudo_r2(final) if final and selected else 0.0},
        "factors": factor_out,
        "threshold": threshold,
        "thresholds": thresholds,
        "timeline": {k_: v for k_, v in timeline.items() if k_ != "bad_zone"},
        "type_associations": type_assoc,
        "verdict": verdict,
        "caveats": base_caveats + auto_caveats,
        "rows": [{"time": r.time, "label": r.label, "n": r.n, "k": r.k, "rate": r.k / r.n, "numeric": r.numeric,
                  "categorical": r.categorical, "top_type": r.top_type,
                  "bad_zone": bool(timeline.get("bad_zone", [False] * len(rows))[i]) if "bad_zone" in timeline else None}
                 for i, r in enumerate(rows)],
    }


def _levels(f: "Factor", rows: list[Row], raw: dict[str, np.ndarray], max_levels: int) -> list[str] | None:
    """요인을 수준으로 본 값 목록. 범주 요인이거나 고유값이 적은 연속 요인(예: 설정값 75/80)만."""
    if f.kind == "categorical":
        return [r.categorical.get(f.name) or "(없음)" for r in rows]
    v = raw[f.name]
    if len(np.unique(v)) <= max_levels:
        return [_fmt(float(x), 3) for x in v]
    return None


def matched_comparison(levels: list[str], primary: np.ndarray, k: np.ndarray, n: np.ndarray, tol: float,
                       expected: np.ndarray | None = None) -> dict | None:
    """층화 비교: 주요인을 폭 2·tol 의 구간으로 나누고, 두 수준 이상이 함께 있는 구간에서만 수준별 불량률을 합산한다.
    → '같은 주요인 값에서도 이 요인에 따라 불량률이 다른가'를 본다.
    expected(주요인만 넣은 모형의 예상 불량 수)가 있으면 구간 안의 남은 주요인 차이까지 보정한 관측/예상 비(O/E)로 비교한다."""
    names = sorted(set(levels))
    if len(names) < 2 or tol <= 0:
        return None
    width = 2 * tol
    lo0 = float(primary.min())
    bins = np.floor((primary - lo0) / width).astype(int)
    strata = []
    sums = {lv: [0.0, 0.0, 0, 0.0] for lv in names}
    for b in sorted(set(bins.tolist())):
        idx = [i for i in range(len(levels)) if bins[i] == b]
        present = {levels[i] for i in idx}
        if len(present) < 2:
            continue
        strata.append([lo0 + b * width, lo0 + (b + 1) * width])
        for i in idx:
            sums[levels[i]][0] += k[i]
            sums[levels[i]][1] += n[i]
            sums[levels[i]][2] += 1
            if expected is not None:
                sums[levels[i]][3] += expected[i]
    out = {lv: {"rows": v[2], "rate": _rate(v[0], v[1]) if v[1] else None,
                "obs_exp": (v[0] / v[3]) if (expected is not None and v[3] > 0) else None} for lv, v in sums.items()}
    key = "obs_exp" if expected is not None else "rate"
    rates = [v[key] for v in out.values() if v[key] is not None]
    comparable = bool(strata) and sum(1 for v in out.values() if v["rows"]) >= 2 and sum(v["rows"] for v in out.values()) >= 3
    rel = (max(rates) - min(rates)) / max(max(rates), 1e-9) if len(rates) >= 2 else None
    rng = [min(a for a, _ in strata), max(b for _, b in strata)] if strata else None
    return {"range": rng, "strata": strata, "levels": out, "comparable": comparable, "max_rel_diff": rel,
            "basis": "관측/예상 비" if expected is not None else "불량률"}


def _threshold(name: str, pv: np.ndarray, k: np.ndarray, n: np.ndarray, direction: int,
               expected: np.ndarray | None = None) -> dict | None:
    """요인을 둘로 나눠 우도 이득이 가장 큰 경계. direction>0 이면 높은 쪽이 나쁨.
    expected(주요인 모형의 예상 불량 수)가 있으면 주요인을 보정한 관측/예상 비로 경계를 찾는다 (보조 요인용)."""
    uniq = np.unique(pv)
    best = None
    for a, b in zip(uniq[:-1], uniq[1:]):
        low = pv <= a
        high = ~low
        if low.sum() < 2 or high.sum() < 2:
            continue
        rl, rh = k[low].sum() / n[low].sum(), k[high].sum() / n[high].sum()
        if expected is None:
            sl, sh = rl, rh
            gain = _split_gain(k, n, low)
        else:
            sl, sh = k[low].sum() / max(expected[low].sum(), 1e-9), k[high].sum() / max(expected[high].sum(), 1e-9)
            gain = _poisson_gain(k, expected, low)
        if (sh - sl) * direction > 0 and (best is None or gain > best[0]):
            best = (gain, float(a), float(b), float(rl), float(rh), int(low.sum()), int(high.sum()))
    if not best:
        return None
    _, a, b, rl, rh, nl, nh = best
    good_low = direction > 0
    return {
        "factor": name, "direction": "high_is_bad" if good_low else "low_is_bad",
        "boundary": (a + b) / 2, "good_side_limit": a if good_low else b,
        "rate_good": rl if good_low else rh, "rate_bad": rh if good_low else rl,
        "rows_good": nl if good_low else nh, "rows_bad": nh if good_low else nl,
        "learned_min": None if good_low else b, "learned_max": a if good_low else None,
    }


def _poisson_gain(k: np.ndarray, e: np.ndarray, mask: np.ndarray) -> float:
    def term(kk, ee):
        return kk * math.log(kk / ee) if kk > 0 and ee > 0 else 0.0

    return term(k[mask].sum(), e[mask].sum()) + term(k[~mask].sum(), e[~mask].sum()) - term(k.sum(), e.sum())


def _split_gain(k: np.ndarray, n: np.ndarray, mask: np.ndarray) -> float:
    def ll(kk, nn):
        p = kk / nn if nn else 0
        if p <= 0 or p >= 1:
            return 0.0
        return kk * math.log(p) + (nn - kk) * math.log(1 - p)

    return ll(k[mask].sum(), n[mask].sum()) + ll(k[~mask].sum(), n[~mask].sum()) - ll(k.sum(), n.sum())


def _mw_p(a: np.ndarray, b: np.ndarray) -> float | None:
    from scipy.stats import mannwhitneyu

    try:
        return float(mannwhitneyu(a, b, alternative="two-sided").pvalue)
    except ValueError:
        return None


def _verdict(rows, n, k, total_rate, primary, selected, terms, final, uni, assessments, threshold, timeline,
             type_assoc, units, value_kinds, cfg) -> dict[str, Any]:
    reasons: list[str] = []
    recs: list[str] = []
    if primary is None:
        return {"headline": f"뚜렷한 원인 요인을 찾지 못했습니다 (전체 불량률 {_pct(total_rate)}). 구간 간 차이가 우연 수준입니다.",
                "confidence": "낮음", "primary": None, "reasons": reasons,
                "recommendations": ["기록 항목(측정값·설정값·작업자·자재 LOT)을 늘리거나 기간을 넓혀 다시 분석하세요."]}
    u = units.get(primary.name) or ""
    kind_txt = {"measured": "측정값", "setpoint": "설정값"}.get(value_kinds.get(primary.name) or "", "")
    p0 = terms[0]["p"]
    r2 = _pseudo_r2(final) if final else 0.0
    if primary.kind == "numeric" and threshold:
        side = "이상" if threshold["direction"] == "high_is_bad" else "이하"
        bad_limit = threshold["boundary"]
        headline = (f"주요 원인 후보: {primary.name}{f'({kind_txt})' if kind_txt else ''}. "
                    f"{primary.name}가 약 {_fmt(bad_limit)}{u} {side}인 구간의 불량률 {_pct(threshold['rate_bad'])}, "
                    f"그 밖 {_pct(threshold['rate_good'])}")
        reasons.append(f"{primary.name} 1σ({_fmt(primary.sd)}{u})당 불량 오즈 {terms[0]['odds_ratio']:.2f}배 "
                       f"(p={_p(p0)}), 이 요인 하나로 구간 간 불량 차이의 {r2 * 100:.0f}% 설명")
    else:
        lv = terms[0]["level"]
        headline = f"주요 원인 후보: {primary.name} = {lv}"
        reasons.append(f"{primary.name} {lv} 의 불량 오즈 {terms[0]['odds_ratio']:.2f}배 (p={_p(p0)})")

    for s in selected[1:]:
        t = next(t for t in terms if t["factor"] == s.name)
        direction = "높을수록" if t["coef"] > 0 else "낮을수록"
        if s.kind == "numeric":
            reasons.append(f"보조 요인: {s.name} — {primary.name}를 보정한 뒤에도 {direction} 불량 증가 (p={_p(t['p'])})")
        else:
            reasons.append(f"보조 요인: {s.name} = {t['level']} (보정 후 p={_p(t['p'])})")

    for name, a in assessments.items():
        if a.get("status") == "EXPLAINED":
            ui = uni[name]
            if a.get("matched"):
                sc = a["similar_condition"]
                parts = ", ".join(f"{lv} {_pct(v['rate'])}({v['rows']}행"
                                  + (f", 예상 대비 {v['obs_exp']:.2f}배" if v.get("obs_exp") is not None else "") + ")"
                                  for lv, v in sc["levels"].items() if v["rows"])
                if "levels" in ui:
                    rates = ", ".join(f"{k_} {_pct(v['rate'])}" for k_, v in ui["levels"].items())
                    head = f"{name}: 겉보기 불량률 차이({rates})는 {sc['factor']}로 설명됨"
                else:
                    head = f"{name}: 단독으로는 불량과 관련(p={_p(ui['p'])})해 보이지만 {sc['factor']}와 함께 움직인 결과"
                reasons.append(f"{head}. 같은 {sc['factor']}({_fmt(sc['range'][0])}~{_fmt(sc['range'][1])}{u})에서 비교하면 "
                               f"{parts} — 차이 없음")
                continue
            if "levels" in ui:
                lv = {k_: v for k_, v in ui["levels"].items()}
                rates = ", ".join(f"{k_} {_pct(v['rate'])}" for k_, v in lv.items())
                msg = f"{name}: 겉보기 불량률 차이({rates})는 {', '.join(a['by'])}로 설명됨 (보정 후 p={_p(a['adjusted_p'])})"
            else:
                msg = (f"{name}: 단독으로는 불량과 관련(p={_p(ui['p'])})해 보이지만 {', '.join(a['by'])}를 보정하면 "
                       f"효과가 {a['shrink'] * 100:.0f}% 줄고 유의하지 않음 (p={_p(a['adjusted_p'])}) → 함께 움직인 결과로 판단")
            sc = a.get("similar_condition")
            if sc:
                parts = ", ".join(f"{lv} {_pct(v['rate'])}({v['rows']}행)" for lv, v in sc["levels"].items() if v["rows"])
                msg += f". 같은 {sc['factor']}({_fmt(sc['range'][0])}~{_fmt(sc['range'][1])}{u})에서 비교: {parts}"
            reasons.append(msg)
        elif a.get("status") == "INSEPARABLE":
            reasons.append(f"{name}: {a['with']}와 거의 같이 움직여(r={a['r']:.2f}) 데이터만으로는 구분 불가 — 따로 바꿔 보는 확인 필요")

    onset = timeline.get("onset")
    first_seen = timeline.get("categorical_first_seen", {})
    if onset:
        msg = f"{primary.name} 변화 시작: {_when(onset['time'])} ({_fmt(onset['value'])}{u}, 기준 평균 {_fmt(onset['baseline_mean'])}{u})"
        for cname, fs in first_seen.items():
            if assessments.get(cname, {}).get("status") in ("EXPLAINED", "NOT_SIGNIFICANT", "INSEPARABLE"):
                order = "이전" if onset["time"] < fs["time"] else "이후"
                msg += f" — {cname} '{fs['level']}' 첫 등장({_when(fs['time'])}) {order}"
        reasons.append(msg)
    for e in timeline.get("events", []):
        msg = (f"설비 이벤트 {e['kind']} {_when(e['start'])}: 전후 불량률 {_pct(e['rate_before'])} → {_pct(e['rate_after'])}")
        if "primary_before" in e:
            msg += f", {primary.name} {_fmt(e['primary_before'])} → {_fmt(e['primary_after'])}{u}"
        reasons.append(msg)
    for t in type_assoc:
        d = "낮을" if t["effect_sd"] < 0 else "높을"
        reasons.append(f"불량 유형 '{t['type']}'가 최다였던 {t['rows']}개 구간은 {t['factor']}가 {d} 때 "
                       f"(평균 {_fmt(t['mean_in'])} vs {_fmt(t['mean_out'])}) — 약한 근거")

    # 권고
    if primary.kind == "numeric" and threshold:
        lim = threshold["learned_max"] if threshold["learned_max"] is not None else threshold["learned_min"]
        bound = "상한" if threshold["learned_max"] is not None else "하한"
        recs.append(f"{primary.name} 관리 {bound} {_fmt(lim)}{u} 를 기준으로 경보를 거세요 (데이터에서 학습한 범위, 현장 검증 후 확정).")
        if value_kinds.get(primary.name) == "measured":
            recs.append(f"{primary.name}는 측정값입니다. 값을 움직인 설비 쪽 원인(온도 조절기, 냉각·가열 회로, 센서)을 점검하세요.")
        elif value_kinds.get(primary.name) == "setpoint":
            recs.append(f"{primary.name}는 설정값입니다. 표준 설정 범위를 정하고 변경 시 승인 절차를 두세요.")
    for e in timeline.get("events", []):
        improved = e["rate_before"] and e["rate_after"] is not None and e["rate_after"] < e["rate_before"] / 2
        if improved and not (e.get("note") and "조치" in e["note"] and "없음" not in e["note"]):
            recs.append(f"{_when(e['start'])} {e['kind']} 직후 불량이 크게 줄었습니다. 그때 무엇을 했는지 확인해 표준 조치로 남기세요.")
    for name, a in assessments.items():
        if a.get("status") == "EXPLAINED" and "levels" in uni[name] and name in first_seen_names(timeline):
            recs.append(f"{name} 자체의 효과는 데이터에서 확인되지 않습니다. 다만 이상이 계속된 동안 감지·대응이 늦었는지 "
                        f"(경보, 인수인계, 점검 주기)는 확인하세요.")
    for t in type_assoc:
        recs.append(f"'{t['type']}' 대응: {t['factor']} 관리도를 함께 보세요 (유형 기록이 최다 유형뿐이라 확인 필요).")
    recs.append("원인을 확인하면 계보 그래프에서 해당 조건/설비를 원인으로 확정해 다음 분석에 반영하세요.")

    n_rows = len(rows)
    if p0 < 0.001 and r2 >= 0.6 and n_rows >= 15:
        conf = "높음"
    elif p0 < 0.01 and r2 >= 0.3:
        conf = "보통"
    else:
        conf = "낮음"
    return {"headline": headline, "confidence": conf, "primary": primary.name, "reasons": reasons,
            "recommendations": recs}


def first_seen_names(timeline: dict) -> set[str]:
    return set(timeline.get("categorical_first_seen", {}))


def _p(p: float) -> str:
    return "<0.001" if p < 0.001 else f"{p:.3f}"
