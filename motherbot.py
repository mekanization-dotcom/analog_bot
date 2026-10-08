#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MotherBot — Falcon Digital→Analog Mapper
=========================================
Layers:
  [D] DIGITAL — همه داده‌های واقعی بک‌تست (بدون ابداع)
  [M] MAPPING — نگاشت دیجیتال به آنالوگ
  [A] ANALOG  — اجرای زنده با پارامترهای واقعی
Run: python motherbot.py
"""
import os, time, re, math, json, random, logging, threading, hashlib, hmac
from collections import deque, defaultdict, OrderedDict
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional, Tuple
import numpy as np, requests
from dotenv import load_dotenv
from flask import Flask, jsonify

load_dotenv()
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s|%(levelname)s|%(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("MB")

# ============================================================
# KEYS
# ============================================================
KEYS = {
    "tg_token": os.getenv("TELEGRAM_TOKEN",""),
    "tg_chat":  os.getenv("TELEGRAM_CHAT_ID",""),
    "toobit_key":    os.getenv("TOOBIT_API_KEY",""),
    "toobit_secret": os.getenv("TOOBIT_API_SECRET",""),
}

# ════════════════════════════════════════════════════════════
# ██ [D] DIGITAL LAYER — داده‌های واقعی بک‌تست
# ════════════════════════════════════════════════════════════
# منبع: لاگ۰۴ §2 + لاگ۰۵ §2 — فقط نتایج تأیید‌شده روی 30 پنجره
# ════════════════════════════════════════════════════════════

# ---- نتایج تأییدشده (30 پنجره، 1080 کندل، 4h) ----
VERIFIED_RESULTS = [
    # (tag, geo_monthly, DD, blew, note)
    ("t71_baseline",       18.11, 73.5, 0, "t71 main"),
    ("t78_plus_whale15",   23.48, 42.1, 0, "⭐ BEST — t78 + WhaleHunter×1.5"),
    ("t78_no_whale",       22.13, 42.1, 1, "t78 بدون whale"),
    ("t78_plus_whale20",   23.12, 42.1, 0, "t78 + Whale×2.0"),
    ("t78_plus_whale30",   20.85, 42.1, 0, "t78 + Whale×3.0"),
    ("t78_whale_filter",   16.55, 48.7, 0, "whale as filter — reject"),
    ("t79_plus_mom12",     22.74, 42.1, 0, "t79 + Mom×1.2"),
    ("t79_plus_mom15",     21.54, 42.1, 0, "t79 + Mom×1.5"),
    ("t79_mom_filter",     17.15, 48.7, 0, "mom as filter — reject"),
    ("t79_plus_markov",    13.96, 61.3, 0, "markov — reject"),
    ("t90f_thompson_m8R50", 6.79, 89.8, 0, "Thompson"),
    ("t90f_thompson_m8R100",5.12, 91.3, 1, "Thompson blew"),
    ("t90f_thompson_m8R100_500sets", 11.61, 64.2, 0, "best adaptive"),
    ("t92_buggy_baseline",  2.59, 74.9, 0, "BUG — _rn broken"),
]

# ---- بهترین پارامتر واقعی (t78 + Whale×1.5) ----
# منبع: لاگ۰۵ §2
BEST_CONFIG = {
    "name": "t78_plus_whale15",
    "geo_monthly": 23.48,
    "DD": 42.1,
    "blew": 0,

    # Flux
    "flux_W": 60, "flux_d": 18, "n_layers": 3,
    "cone_max": 2, "flux_min": 0.25, "edge_min": 0.10,

    # K indicator
    "K_W": 30,
    "Kth_pct": 0.75, "Ktl_pct": 0.25, "K_window": 500,

    # Trade
    "risk": 0.20, "mc": 4, "fut": 24,
    "sl_atr": 8.0, "rr": 2.0, "msp": 0.95,
    "MAX_TOTAL_RISK": 2.00,          # ← CRITICAL FIX
    "fee_round_trip": 0.0012,

    # Whale
    "whale_thr_pct": 0.50, "whale_mult": 1.5, "whale_filter": False,

    # Momentum
    "mom_lookbacks": [6, 24, 72], "mom_mult": 1.2,

    # Regime (BTC 200-bar)
    "regime_lb": 200,
    "regime_bull": 0.05, "regime_bear_lo": -0.10, "regime_bear_hi": -0.05,

    # TF & windows
    "tf": "4h", "window_bars": 1080,
    "seed1": 42, "n1": 10, "seed2": 99, "n2": 20,
}

# ---- 30 استراتژی (منبع: لاگ۰۵ §6 — فقط اعداد تأییدشده) ----
# fields: tier, risk, adx, mult, mc, sl, rr, fut, geo, dd
STRATEGIES = {
    # SAFE — DD < 70%
    "S01": ("SAFE", 0.20, 10, 5.0, 3, 8.0, 2.0, 24, 15.48, 65.9),
    "S02": ("SAFE", 0.20, 8, 12.0, 3, 8.0, 2.0, 24, 17.42, 68.5),
    "S03": ("SAFE", 0.20, 10, 5.0, 3, 8.0, 2.0, 24, 16.68, 63.7),
    "S04": ("SAFE", 0.20, 10, 5.0, 3, 8.0, 2.0, 24, 14.39, 91.0),
    "S05": ("SAFE", 0.20, 15, 3.0, 3, 8.0, 2.0, 24, 12.89, 65.1),
    "S06": ("SAFE", 0.20, 25, 3.0, 3, 8.0, 2.0, 24, 11.97, 65.1),
    "S07": ("SAFE", 0.20, 10, 5.0, 3, 8.0, 2.0, 24,  9.60, 79.1),
    "S08": ("SAFE", 0.20, 10, 5.0, 3, 8.0, 2.0, 24,  9.30, 49.8),
    "S09": ("SAFE", 0.20, 999,1.0, 3, 8.0, 2.0, 24,  7.71, 55.4),
    "S10": ("SAFE", 0.20, 10, 5.0, 3, 8.0, 2.0, 24,  7.28, 69.9),
    # MID — DD 70-80%
    "M01": ("MID", 0.20, 8, 8.0, 3, 8.0, 2.0, 24, 18.11, 73.5),
    "M02": ("MID", 0.45, 10, 3.0, 6, 8.0, 2.0, 24, 17.91, 81.9),
    "M03": ("MID", 0.30, 12, 4.0, 3, 8.0, 2.0, 24, 17.39, 74.4),
    "M04": ("MID", 0.30, 12, 4.0, 3, 8.0, 2.0, 24, 17.39, 74.4),
    "M05": ("MID", 0.30, 10, 5.0, 3, 8.0, 2.0, 24, 17.30, 78.3),
    "M06": ("MID", 0.30, 12, 3.5, 3, 8.0, 2.0, 24, 17.29, 71.6),
    "M07": ("MID", 0.30, 15, 4.0, 3, 8.0, 2.0, 24, 17.28, 74.4),
    "M08": ("MID", 0.32, 10, 3.0, 6, 8.0, 2.0, 24, 16.85, 71.7),
    "M09": ("MID", 0.30, 18, 4.0, 3, 8.0, 2.0, 24, 16.84, 74.4),
    "M10": ("MID", 0.40, 12, 3.0, 3, 8.0, 2.0, 24, 17.77, 82.2),
    # AGGRESSIVE — DD > 80%
    "A01": ("AGGR", 0.60, 25, 3.0, 3, 8.0, 2.0, 24, 22.81, 91.9),
    "A02": ("AGGR", 0.60, 30, 3.0, 3, 8.0, 2.0, 24, 21.30, 91.1),
    "A03": ("AGGR", 0.30, 25, 3.0, 3, 8.0, 2.0, 24, 18.53, 83.1),
    "A04": ("AGGR", 0.40, 12, 3.0, 3, 8.0, 2.0, 24, 17.77, 82.2),
    "A05": ("AGGR", 0.30, 15, 5.0, 3, 8.0, 2.0, 24, 16.72, 78.3),
    "A06": ("AGGR", 1.00, 10, 5.0, 3, 8.0, 2.0, 24, 15.52, 76.6),
    "A07": ("AGGR", 0.60, 999,1.0, 3, 8.0, 2.0, 24, 15.08, 91.1),
    "A08": ("AGGR", 0.30, 10, 5.0, 3, 8.0, 2.0, 24, 15.48, 65.9),
    "A09": ("AGGR", 0.60, 25, 3.0, 3, 8.0, 2.0, 24, 11.87, 92.5),
    "A10": ("AGGR", 0.60, 10, 3.0, 6, 8.0, 2.0, 24, 11.78, 92.5),
}

# ---- TripleSafe (t71) ----
TRIPLE_SAFE = {
    "MULT": 8.0, "ADX": 8.0,
    "DD_LOW": 8.0, "DD_MID": 12.0, "DD_HIGH": 25.0,
}

# ---- Falcon v5.4 Switcher (تعدیل‌شده با نتایج واقعی) ----
SWITCHER_MODES = {
    "AGGRESSIVE": {"lev":3.0, "risk":0.30, "max_pos":6, "max_dd":0.80, "daily_dd":0.15},
    "BALANCED":   {"lev":1.5, "risk":0.20, "max_pos":4, "max_dd":0.70, "daily_dd":0.10},
    "SAFE":       {"lev":1.0, "risk":0.15, "max_pos":3, "max_dd":0.55, "daily_dd":0.06},
}

# ---- باگ‌های کشف‌شده (فقط برای مستندسازی) ----
KNOWN_BUGS = {
    "BUG1_rn": "s/W نه s/cnt + گارد n<W+5 و cnt<5",
    "BUG2_mtr": "MAX_TOTAL_RISK=0.60 → باید 2.00",
    "BUG3_ks": "(tl-Ki)/(th-th) → باید (tl-Ki)/(th-tl)",
    "BUG4_pei": "pei[no]=je حذف شده",
    "BUG5_lev": "leverage 2865x (div-by-zero)",
}

# ---- ۳۰ پنجره: seeds و bars (لاگ۰۴ §0) ----
WINDOWS = {
    "bars": 1080,
    "seed1": 42, "n1": 10,
    "seed2": 99, "n2": 20,
    "metric": "geo_monthly = mean(((r/100+1)^(1/6)-1)*100 for r>-100)",
}

# ════════════════════════════════════════════════════════════
# ██ [M] MAPPING LAYER — دیجیتال → آنالوگ
# ════════════════════════════════════════════════════════════

class DigitalStore:
    """لایه دیجیتال: همه داده‌های واقعی بک‌تست."""
    def __init__(self):
        self.results = list(VERIFIED_RESULTS)
        self.best = dict(BEST_CONFIG)
        self.strategies = dict(STRATEGIES)
        self.triple_safe = dict(TRIPLE_SAFE)
        self.switcher = dict(SWITCHER_MODES)
        self.bugs = dict(KNOWN_BUGS)
        self.windows = dict(WINDOWS)

    def get_best_config(self) -> Dict:
        return dict(self.best)

    def get_strategy(self, sid: str) -> Optional[Dict]:
        if sid not in self.strategies: return None
        tier, risk, adx, mult, mc, sl, rr, fut, geo, dd = self.strategies[sid]
        return {"id":sid,"tier":tier,"risk":risk,"adx":adx,"mult":mult,
                "mc":mc,"sl":sl,"rr":rr,"fut":fut,"geo":geo,"dd":dd}

    def list_by_tier(self, tier: str) -> List[str]:
        return [sid for sid,(t,*_) in self.strategies.items() if t == tier]

    def best_by_dd(self, max_dd: float) -> Optional[str]:
        """بهترین استراتژی که DD زیر آستانه داره."""
        candidates = [(sid, s) for sid, s in self.strategies.items() if s[9] <= max_dd]
        if not candidates: return None
        return max(candidates, key=lambda x: x[1][8])[0]  # بالاترین geo

    def to_json(self) -> str:
        return json.dumps({
            "results": self.results, "best": self.best,
            "strategies": self.strategies, "triple_safe": self.triple_safe,
            "switcher": self.switcher, "windows": self.windows,
        }, default=str, ensure_ascii=False, indent=2)

DIGITAL = DigitalStore()


class AnalogMapper:
    """
    لایه نگاشت: داده دیجیتال → پارامتر آنالوگ زنده.
    بدون ابداع هیچ مقدار. فقط تبدیل واحد و ساختار.
    """
    def __init__(self, digital: DigitalStore):
        self.d = digital

    def map_best(self) -> Dict:
        """نگاشت بهترین کانفیگ (t78 + Whale×1.5) به پارامترهای اجرا."""
        b = self.d.get_best_config()
        return {
            # indicators
            "flux_W": b["flux_W"], "flux_d": b["flux_d"],
            "cone_max": b["cone_max"], "flux_min": b["flux_min"],
            "edge_min": b["edge_min"], "K_W": b["K_W"],
            "Kth_pct": b["Kth_pct"], "Ktl_pct": b["Ktl_pct"],
            "K_window": b["K_window"],
            # trade
            "risk": b["risk"], "mc": b["mc"], "fut": b["fut"],
            "sl_atr": b["sl_atr"], "rr": b["rr"], "msp": b["msp"],
            "max_total_risk": b["MAX_TOTAL_RISK"],
            "fee": b["fee_round_trip"],
            # whale
            "whale_thr": b["whale_thr_pct"], "whale_mult": b["whale_mult"],
            "whale_filter": b["whale_filter"],
            # momentum
            "mom_mult": b["mom_mult"], "mom_lookbacks": b["mom_lookbacks"],
            # regime
            "regime_lb": b["regime_lb"],
            "regime_bull": b["regime_bull"],
            "regime_bear_lo": b["regime_bear_lo"],
            "regime_bear_hi": b["regime_bear_hi"],
            # tf
            "tf": b["tf"],
            # meta
            "_source": b["name"], "_geo": b["geo_monthly"], "_dd": b["DD"],
        }

    def map_strategy(self, sid: str) -> Dict:
        """نگاشت استراتژی خاص."""
        s = self.d.get_strategy(sid)
        if not s: return {}
        return {
            "id": s["id"], "tier": s["tier"],
            "risk": s["risk"], "adx_min": s["adx"], "triple_mult": s["mult"],
            "mc": s["mc"], "sl_atr": s["sl"], "rr": s["rr"], "fut": s["fut"],
            "expected_geo": s["geo"], "expected_dd": s["dd"],
        }

    def map_switcher(self) -> Dict:
        """نگاشت سوییچر v5.4 (تعدیل‌شده با نتایج واقعی)."""
        return dict(self.d.switcher)

    def pick_for_current(self, dd: float, regime: str, vol: float) -> str:
        """کدام استراتژی برای شرایط فعلی."""
        # اولویت: کمترین DD ممکن
        if regime in ("CRISIS", "BEAR_SEVERE"):
            # فقط SAFE با DD < 70
            return self.d.best_by_dd(70.0) or "S01"
        if regime in ("RANGE", "BEAR_MILD"):
            # MID با DD < 80
            return self.d.best_by_dd(80.0) or "M01"
        if regime in ("BULL_MILD", "BULL_STRONG"):
            # AGGRESSIVE
            best_sid = max(STRATEGIES.items(),
                          key=lambda x: x[1][8])[0]  # بالاترین geo
            return best_sid
        return "S02"  # پیش‌فرض: t71 (SAFE با geo 17.42)

MAPPER = AnalogMapper(DIGITAL)

# ════════════════════════════════════════════════════════════
# ██ [A] ANALOG ENGINE — اجرای زنده
# ════════════════════════════════════════════════════════════

# ---- numba-friendly K indicator ----
def _ls(x): return np.sign(x)*np.log1p(np.abs(x))

def _rn(x, W):
    """Rolling z-score با گاردهای درست (BUG1 fix)."""
    n = len(x); o = np.full(n, np.nan)
    if n < W + 5: return o                        # guard1
    for i in range(W, n):
        cnt = 0; s = 0.0; s2 = 0.0
        for j in range(i-W, i):
            if not np.isnan(x[j]):
                s += x[j]; s2 += x[j]*x[j]; cnt += 1
        if cnt < 5: continue                      # guard2
        m = s/cnt; v = s2/cnt - m*m               # s/cnt نه s/W
        if v > 0: o[i] = x[i]/(math.sqrt(v)+1e-9)
    return o

def compute_flux(c, o, h, l, v, W, d):
    """Flux 3-level (X/Y/Z) — نسخه canonical (BUG1 fix)"""
    n = len(c)
    fx  = (c+o)/2.0; fxp  = (c-o)/2.0
    f2x = np.full(n, np.nan); f2xp = np.full(n, np.nan)
    f2x[d:] = fx[:-d]; f2xp[d:] = fxp[:-d]
    gx = (fx-f2x)/d; gxp = (fxp-f2xp)/d
    SX = (gx+gxp)/2.0; NX = (gx-gxp)/2.0

    cs = np.cumsum(v); vm = np.full(n, np.nan)
    if n > W: vm[W:] = (cs[W:]-cs[:-W])/W
    vn = v/(vm+1e-9)
    csv = np.cumsum(vn); vf = np.full(n, np.nan)
    if n > 5: vf[5:] = (csv[5:]-csv[:-5])/5
    fy = (vn+vf)/2.0; fyp = (vn-vf)/2.0
    f2y = np.full(n, np.nan); f2yp = np.full(n, np.nan)
    f2y[d:] = fy[:-d]; f2yp[d:] = fyp[:-d]
    gy = (fy-f2y)/d; gyp = (fyp-f2yp)/d
    SY = (gy+gyp)/2.0; NY = (gy-gyp)/2.0

    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    ct = np.cumsum(tr); atr = np.full(n, np.nan)
    if n > W: atr[W:] = (ct[W:]-ct[:-W])/W
    an = atr/(c+1e-9)
    r1 = np.zeros(n)
    for i in range(1, n):
        if c[i-1] > 0: r1[i] = (c[i]-c[i-1])/c[i-1]
    csr = np.cumsum(r1); rf = np.full(n, np.nan)
    if n > 5: rf[5:] = (csr[5:]-csr[:-5])/5
    fz = (an+rf)/2.0; fzp = (an-rf)/2.0
    f2z = np.full(n, np.nan); f2zp = np.full(n, np.nan)
    f2z[d:] = fz[:-d]; f2zp[d:] = fzp[:-d]
    gz = (fz-f2z)/d; gzp = (fzp-f2zp)/d
    SZ = (gz+gzp)/2.0; NZ = (gz-gzp)/2.0

    SXn = _rn(_ls(SX), W); NXn = _rn(_ls(NX), W)
    SYn = _rn(_ls(SY), W); NYn = _rn(_ls(NY), W)
    SZn = _rn(_ls(SZ), W); NZn = _rn(_ls(NZ), W)

    fd = np.zeros(n, dtype=int); fs = np.zeros(n)
    ra = np.zeros(n); ed = np.zeros(n)
    for i in range(n):
        sx = SXn[i] if not np.isnan(SXn[i]) else 0.0
        sy = SYn[i] if not np.isnan(SYn[i]) else 0.0
        sz = SZn[i] if not np.isnan(SZn[i]) else 0.0
        nx = NXn[i] if not np.isnan(NXn[i]) else 0.0
        ny = NYn[i] if not np.isnan(NYn[i]) else 0.0
        nz = NZn[i] if not np.isnan(NZn[i]) else 0.0
        st = sx+sy+sz
        fd[i] = 1 if st > 0 else (-1 if st < 0 else 0)
        fs[i] = abs(st)
        nS = math.sqrt(sx*sx+sy*sy+sz*sz)
        nN = math.sqrt(nx*nx+ny*ny+nz*nz)
        ra[i] = nS/(nS+nN+1e-9)
        snr = nS/(nN+1e-9); sh = nN/(nS+1e-9)
        ed[i] = (snr/(1+snr))*ra[i]*(1-min(max(sh, 0.0), 1.0))
    return fd, fs, ra, ed, atr

def cone_layer(ra, nl):
    n = len(ra); L = np.zeros(n, dtype=int)
    for i in range(n):
        if np.isnan(ra[i]): continue
        for k in range(nl):
            if k/nl <= ra[i] < (k+1)/nl:
                L[i] = k; break
    return L

def whale_acc(c, v):
    n = len(c); S = np.zeros(n)
    for i in range(3, n):
        if c[i-1] > 0 and c[i-2] > 0:
            aP = (c[i]-c[i-1])/c[i-1] - (c[i-1]-c[i-2])/c[i-2]
        else: aP = 0.0
        aV = (v[i]-v[i-1]) - (v[i-1]-v[i-2])
        N = math.exp(min(v[i], 5.0))/math.log1p(v[i]) if 0 < v[i] < 100 else 1.0
        if aP > 0 and aV > 0: S[i] = N*aP*aV*100.0
    return S

def K_indicator(c, h, l, v, W):
    n = len(c); K = np.full(n, np.nan)
    for i in range(W, n):
        rs = 0.0; rsq = 0.0
        for j in range(i-W, i):
            r = math.log1p(max(v[j], 0.0))
            rs += r; rsq += r*r
        mu = rs/W
        sd = math.sqrt(max(rsq/W - mu*mu, 0.0)) + 1e-9
        I1 = 0.0; I2 = 0.0; I3 = 0.0
        vm = sum(v[i-W:i])/W + 1e-9
        for j in range(i-W+1, i):
            v1 = 1.0/(1.0 + math.exp(-max(-30, min(30, (math.log1p(max(v[j-1],0))-mu)/sd))))
            v2 = 1.0/(1.0 + math.exp(-max(-30, min(30, (math.log1p(max(v[j],0))-mu)/sd))))
            I1 += (math.exp(min(50, 3*v1)) + math.exp(min(50, 3*v2)))/2
            I2 += (math.log1p(v1) + math.log1p(v2))/2
            I3 += ((v[j-1]/vm) + (v[j]/vm))/2
        hm = max(h[i-W:i]); lm = min(l[i-W:i])
        A = (hm-lm)/max(c[i-1], 1e-9)
        K[i] = (0.33/1.33)*(I1/max(I2, 1e-9)) + (0.67/1.33)*I2 + (0.33/1.33)*(I3*A)
    return K

def rolling_quantile(arr, window, q):
    n = len(arr); o = np.full(n, np.nan)
    for i in range(window, n):
        chunk = arr[i-window:i]
        chunk = chunk[~np.isnan(chunk)]
        if len(chunk) < 50: continue
        o[i] = np.quantile(chunk, q)
    return o

def detect_regime(btc, lb):
    n = len(btc); R = np.zeros(n, dtype=bool)
    for i in range(lb, n):
        r = (btc[i] - btc[i-lb]) / max(btc[i-lb], 1e-9)
        R[i] = (r > 0.05) or (-0.10 < r < -0.05)
    return R

# ---- Exchange Stub (paper) ----
class ToobitClient:
    def __init__(self, key="", secret=""):
        self.key, self.secret = key, secret
        self.base = "https://api.toobit.com"
    def fetch_ohlcv(self, sym, tf, limit=500):
        now = int(time.time()*1000); px = 100.0; out = []
        for i in range(limit):
            o = px; h = o+abs(random.gauss(0,0.3))
            l = o-abs(random.gauss(0,0.3)); c = o+random.gauss(0,0.3)
            v = abs(random.gauss(1000,200))
            out.append([now-(limit-i)*300_000, o, h, l, c, v]); px = c
        return out
    def fetch_ticker(self, sym):
        return {"last": 100.0 + random.gauss(0, 0.5)}
    def create_order(self, sym, side, size):
        if CFG_PAPER:
            log.info(f"[PAPER] {side.upper()} {sym} sz={size:.4f}")
            return {"id": f"P{int(time.time()*1000)}"}
        return {"error": "live not configured"}

CFG_PAPER = os.getenv("PAPER", "true").lower() == "true"
EX = ToobitClient(KEYS["toobit_key"], KEYS["toobit_secret"])

# ---- Indicator cache ----
class IndicatorCache:
    def __init__(self):
        self.data = {}  # sym -> {"K":..., "fd":..., "fs":..., "ra":..., "ed":..., "atr":..., "Wa":..., "Kth":..., "Ktl":...}
        self.lock = threading.Lock()

    def update(self, sym, ohlcv):
        c = np.array([x[4] for x in ohlcv], dtype=float)
        o = np.array([x[1] for x in ohlcv], dtype=float)
        h = np.array([x[2] for x in ohlcv], dtype=float)
        l = np.array([x[3] for x in ohlcv], dtype=float)
        v = np.array([x[5] for x in ohlcv], dtype=float)

        b = DIGITAL.best
        fd, fs, ra, ed, atr = compute_flux(c, o, h, l, v, b["flux_W"], b["flux_d"])
        L = cone_layer(ra, b["n_layers"])
        K = K_indicator(c, h, l, v, b["K_W"])
        Wa = whale_acc(c, v)
        Kth = rolling_quantile(K, b["K_window"], b["Kth_pct"])
        Ktl = rolling_quantile(K, b["K_window"], b["Ktl_pct"])

        with self.lock:
            self.data[sym] = {
                "c":c, "o":o, "h":h, "l":l, "v":v,
                "fd":fd, "fs":fs, "ra":ra, "ed":ed, "atr":atr,
                "L":L, "K":K, "Wa":Wa, "Kth":Kth, "Ktl":Ktl,
                "Wa_thr": float(np.percentile(Wa[Wa>0], 50)) if (Wa>0).sum() > 10 else 0.0,
            }

    def get(self, sym):
        with self.lock:
            return self.data.get(sym)

CACHE = IndicatorCache()

# ---- Strategy Runner (Mapped execution) ----
class AnalogRunner:
    """اجرا با پارامترهای mapped از digital."""
    def __init__(self):
        self.mapped = MAPPER.map_best()
        self.current_sid = "M01"          # t71 (SAFE با geo 17.42)
        self.strategy = MAPPER.map_strategy(self.current_sid)
        log.info(f"[ANALOG] mapped from {self.mapped['_source']} geo={self.mapped['_geo']}%")
        log.info(f"[ANALOG] strategy: {self.current_sid} tier={self.strategy['tier']}")

    def switch_strategy(self, sid: str):
        s = MAPPER.map_strategy(sid)
        if s:
            self.strategy = s; self.current_sid = sid
            log.info(f"[SWITCH] → {sid} ({s['tier']})")

    def find_signal(self, sym: str) -> Optional[Dict]:
        d = CACHE.get(sym)
        if d is None: return None
        i = len(d["c"]) - 1
        if i < 200: return None

        K = d["K"][i]
        if np.isnan(K): return None
        th = d["Kth"][i]; tl = d["Ktl"][i]
        if np.isnan(th) or np.isnan(tl): return None

        direction = 0
        if K > th: direction = 1
        elif K < tl: direction = -1
        if direction == 0: return None

        # Flux direction confirm
        if d["fd"][i] != direction: return None
        if d["fs"][i] < self.mapped["flux_min"]: return None

        # Cone layer
        if d["L"][i] > self.mapped["cone_max"]: return None

        # Edge
        if np.isnan(d["ed"][i]) or d["ed"][i] < self.mapped["edge_min"]: return None

        # score (BUG3 fix: th-tl نه th-th)
        ks = (K-th)/(th-tl+1e-9) if direction == 1 else (tl-K)/(th-tl+1e-9)
        score = min(ks, 3.0) * (0.5 + 0.5*d["ed"][i])

        # Whale boost (not filter)
        Wa = d["Wa"][i]
        whale = False
        if not np.isnan(Wa) and Wa > d["Wa_thr"]:
            score *= self.mapped["whale_mult"]  # 1.5
            whale = True

        return {
            "sym": sym, "direction": direction, "K": float(K),
            "score": float(score), "whale": whale,
            "price": float(d["c"][i]),
            "atr": float(d["atr"][i]),
        }

RUNNER = AnalogRunner()

# ════════════════════════════════════════════════════════════
# State / Portfolio
# ════════════════════════════════════════════════════════════
class State:
    def __init__(self):
        self.lk = threading.Lock()
        self.cap = 1000.0; self.pk = 1000.0; self.pnl = 0.0
        self.td = 0; self.wd = 0; self.consec_losses = 0
        self.ps: List[Dict[str,Any]] = []
        self.paused = False
        self.mother_state = "INIT"
        self.mode = "BALANCED"
        self.daily_start_cap = 1000.0
        self.daily_reset_ts = time.time()
    def dd(self):
        return (self.pk - self.cap) / max(self.pk, 1e-9)
    def daily_dd(self):
        return (self.daily_start_cap - self.cap) / max(self.daily_start_cap, 1e-9)
ST = State()

# ════════════════════════════════════════════════════════════
# Switcher — v5.4 adjusted
# ════════════════════════════════════════════════════════════
def pick_mode(dd: float, regime: str) -> str:
    if dd >= 0.55 or regime in ("CRISIS",):
        return "SAFE"
    if dd >= 0.30 or regime in ("RANGE","BEAR_MILD","BEAR_SEVERE"):
        return "BALANCED"
    if regime in ("BULL_MILD","BULL_STRONG") and dd < 0.20:
        return "AGGRESSIVE"
    return "BALANCED"

def apply_mode(mode: str):
    cfg = MAPPER.map_switcher().get(mode, MAPPER.map_switcher()["BALANCED"])
    ST.mode = mode
    # pick best strategy for mode
    if mode == "SAFE":
        sid = MAPPER.d.best_by_dd(70.0) or "S01"
    elif mode == "BALANCED":
        sid = "M01"  # t71
    else:  # AGGRESSIVE
        sid = "A01"  # best geo
    RUNNER.switch_strategy(sid)

# ════════════════════════════════════════════════════════════
# MAIN LOOP
# ════════════════════════════════════════════════════════════

def get_regime_label(sym="BTCUSDT") -> str:
    d = CACHE.get(sym)
    if not d: return "RANGE"
    btc = d["c"]
    if len(btc) < 220: return "RANGE"
    r = (btc[-1] - btc[-200]) / max(btc[-200], 1e-9)
    if r > 0.10: return "BULL_STRONG"
    if r > 0.05: return "BULL_MILD"
    if r < -0.10: return "BEAR_SEVERE"
    if r < -0.05: return "BEAR_MILD"
    return "RANGE"

def main_loop():
    log.info("MOTHERBOT DIGITAL→ANALOG STARTED")
    last_scan = 0.0
    while True:
        try:
            # daily reset
            if time.time() - ST.daily_reset_ts > 86400:
                ST.daily_start_cap = ST.cap
                ST.daily_reset_ts = time.time()

            if ST.paused:
                time.sleep(5); continue

            # 1) update cache for all symbols
            for sym in BEST_CONFIG.get("sym", CFG["sym"]) if False else ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","DOTUSDT"]:
                try:
                    ohlcv = EX.fetch_ohlcv(sym, "4h", 500)
                    CACHE.update(sym, ohlcv)
                except Exception as e:
                    log.warning(f"[CACHE] {sym}: {e}")

            # 2) regime & mode
            regime = get_regime_label("BTCUSDT")
            mode = pick_mode(ST.dd(), regime)
            if mode != ST.mode:
                apply_mode(mode)
                LOGS_add("RUP", f"mode → {mode} (regime={regime}, dd={ST.dd():.3f})")

            # 3) manage open positions
            with ST.lk: ps = list(ST.ps)
            for p in ps:
                t = EX.fetch_ticker(p["sym"])
                if not t: continue
                px = t["last"]
                hit = False; ep=None; rs=None
                if p["d"] == 1:
                    if px <= p["sl"]: hit,ep,rs = True,p["sl"],"SL"
                    elif px >= p["tp"]: hit,ep,rs = True,p["tp"],"TP"
                else:
                    if px >= p["sl"]: hit,ep,rs = True,p["sl"],"SL"
                    elif px <= p["tp"]: hit,ep,rs = True,p["tp"],"TP"
                if hit:
                    EX.create_order(p["sym"], "sell" if p["d"]==1 else "buy", p["sz"])
                    pp = (ep-p["e"])/p["e"] if p["d"]==1 else (p["e"]-ep)/p["e"]
                    pn = (pp - 0.0012)*p["sz"]*p["e"]
                    with ST.lk:
                        ST.cap += pn; ST.pnl += pn
                        ST.pk = max(ST.pk, ST.cap)
                        ST.consec_losses = 0 if pn>0 else ST.consec_losses+1
                        if p in ST.ps: ST.ps.remove(p)
                        if pn > 0: ST.wd += 1
                    LOGS_add("CLS", f"{p['sym']} [{rs}] PnL={pn:+.2f}")

            # 4) scan for new (every 60s)
            if time.time() - last_scan >= 60:
                # daily_dd check
                if ST.daily_dd() >= 0.10:
                    last_scan = time.time(); time.sleep(5); continue
                if ST.dd() >= 0.55:
                    LOGS_add("WRN", f"DD {ST.dd():.3f} → no new entry")
                    last_scan = time.time(); time.sleep(5); continue

                # total risk check (BUG2 fix: 2.00 نه 0.60)
                total_risk = 0.0
                with ST.lk:
                    for p in ST.ps:
                        total_risk += p["sz"] * abs(p["e"]-p["sl"]) / max(p["e"],1e-9) / max(ST.cap,1e-9)

                signals = []
                for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","DOTUSDT"]:
                    sig = RUNNER.find_signal(sym)
                    if sig: signals.append(sig)
                signals.sort(key=lambda x: -x["score"])

                with ST.lk:
                    slots = RUNNER.strategy["mc"] - len(ST.ps)
                for sig in signals[:max(0, slots)]:
                    if total_risk + RUNNER.strategy["risk"] > 2.00: break
                    sym = sig["sym"]; d = sig["direction"]; e = sig["price"]; a = sig["atr"]
                    if a <= 0: continue
                    sl_d = a * RUNNER.strategy["sl_atr"]
                    tp_d = sl_d * RUNNER.strategy["rr"]
                    sl = e - sl_d if d == 1 else e + sl_d
                    tp = e + tp_d if d == 1 else e - tp_d
                    spc = abs(e - sl)/e
                    if spc < 1e-9: continue
                    sz = (ST.cap * RUNNER.strategy["risk"]) / spc
                    if sz > ST.cap * 0.95 / e: sz = ST.cap * 0.95 / e
                    pos = {"id": ST.td+1, "sym": sym, "d": d, "e": e,
                           "sl": sl, "tp": tp, "sz": sz,
                           "sid": RUNNER.current_sid}
                    with ST.lk:
                        ST.ps.append(pos); ST.td += 1
                    LOGS_add("OPN", f"#{pos['id']} {sym} {'LONG' if d==1 else 'SHORT'} sid={RUNNER.current_sid} sz={sz:.4f} whale={sig['whale']}")
                    TG_NOTIFY(f"🟢 OPEN #{pos['id']} {sym} {'LONG' if d==1 else 'SHORT'}\nsid={RUNNER.current_sid} score={sig['score']:.2f}")
                last_scan = time.time()

            time.sleep(5)
        except KeyboardInterrupt: break
        except Exception as e:
            log.error(f"[LOOP] {e}", exc_info=True); time.sleep(5)

# ════════════════════════════════════════════════════════════
# Telegram
# ════════════════════════════════════════════════════════════
class LogBuffer:
    def __init__(self, n=500):
        self.buf = deque(maxlen=n); self.lk = threading.Lock()
    def add(self, code, msg):
        with self.lk: self.buf.append({"ts":time.time(),"code":code,"msg":msg})
    def recent(self, n=20, code=None):
        with self.lk: items = list(self.buf)
        if code: items = [x for x in items if x["code"] == code]
        items = items[-n:]
        if not items: return "لاگ خالی"
        return "\n".join(f"[{time.strftime('%H:%M:%S',time.localtime(x['ts']))}][{x['code']}] {x['msg']}" for x in items)
LOGS = LogBuffer()
def LOGS_add(c, m): LOGS.add(c, m)

class TelegramBot:
    def __init__(self, token, chat_id):
        self.token = token; self.chat_id = str(chat_id)
        self.offset = 0; self.base = f"https://api.telegram.org/bot{token}"
    def send(self, text):
        if not self.token: return
        try:
            requests.post(f"{self.base}/sendMessage",
                json={"chat_id":self.chat_id,"text":text,"parse_mode":"HTML"}, timeout=10)
        except Exception as e: log.warning(f"[TG] {e}")
    def get_updates(self):
        try:
            r = requests.get(f"{self.base}/getUpdates",
                params={"offset":self.offset,"timeout":20}, timeout=25)
            return r.json().get("result", []) if r.status_code==200 else []
        except: return []
    def handle(self, text, uid):
        if str(uid) != self.chat_id: return
        text = text.strip()
        sp = text.split(maxsplit=1); cmd = sp[0].lower(); arg = sp[1] if len(sp)>1 else ""
        if cmd in ("/help","/start"): self.send(HELP)
        elif cmd == "/status":
            with ST.lk:
                self.send(
                    f"<b>📊 MotherBot</b>\n"
                    f"Mode: <code>{ST.mode}</code>\n"
                    f"Strategy: <code>{RUNNER.current_sid}</code> ({RUNNER.strategy['tier']})\n"
                    f"Geo expected: <b>{RUNNER.strategy['expected_geo']:+.2f}%</b>/mo\n"
                    f"DD expected: {RUNNER.strategy['expected_dd']:.1f}%\n"
                    f"Capital: {ST.cap:.2f} | PnL: {ST.pnl:+.2f}\n"
                    f"DD: {ST.dd()*100:.2f}% | Daily DD: {ST.daily_dd()*100:.2f}%\n"
                    f"Positions: {len(ST.ps)} | Trades: {ST.td} | Wins: {ST.wd}\n"
                    f"Source: <code>{RUNNER.mapped['_source']}</code>")
        elif cmd == "/best":
            b = DIGITAL.best
            self.send(
                f"<b>⭐ Best Verified</b>\n"
                f"Name: <code>{b['name']}</code>\n"
                f"Geo: <b>{b['geo_monthly']}%</b>/mo\n"
                f"DD: {b['DD']}% | Blew: {b['blew']}\n"
                f"risk={b['risk']} mc={b['mc']} sl={b['sl_atr']} rr={b['rr']}\n"
                f"flux_W={b['flux_W']} flux_d={b['flux_d']}\n"
                f"whale_mult={b['whale_mult']} (filter={b['whale_filter']})\n"
                f"MAX_TOTAL_RISK={b['MAX_TOTAL_RISK']} (fixed)")
        elif cmd == "/strategies":
            tiers = {"SAFE":[],"MID":[],"AGGR":[]}
            for sid, s in STRATEGIES.items():
                tiers[s[0]].append(f"  <code>{sid}</code> geo={s[8]:+.2f} dd={s[9]:.1f}")
            self.send("<b>30 Strategies</b>\n" +
                "\n".join(f"<b>{k}</b>\n" + "\n".join(v) for k,v in tiers.items()))
        elif cmd == "/switch":
            sid = arg.strip().upper()
            if sid in STRATEGIES:
                RUNNER.switch_strategy(sid); self.send(f"✅ → {sid}")
            else: self.send(f"❌ نامعتبر: {sid}")
        elif cmd == "/modes":
            self.send("Modes:\n" + "\n".join(f"<code>{k}</code> lev={v['lev']} risk={v['risk']} mc={v['max_pos']}" for k,v in SWITCHER_MODES.items()))
        elif cmd == "/bugs":
            self.send("<b>Known Bugs (fixed in analog)</b>\n" +
                "\n".join(f"<code>{k}</code>: {v}" for k,v in KNOWN_BUGS.items()))
        elif cmd == "/results":
            out = ["<b>Verified Results (30 windows)</b>"]
            for tag,geo,dd,blw,note in VERIFIED_RESULTS:
                flag = "⭐" if "BEST" in note else ("❌" if "reject" in note or "BUG" in note else "")
                out.append(f"  {flag} <code>{tag}</code> geo={geo:+.2f}% DD={dd:.1f}% blew={blw}")
            self.send("\n".join(out))
        elif cmd == "/log":
            sp2 = arg.split()
            code = sp2[0] if sp2 and not sp2[0].isdigit() else None
            n = int(sp2[-1]) if sp2 and sp2[-1].isdigit() else 15
            self.send(f"<pre>{LOGS.recent(n, code)}</pre>")
        elif cmd == "/pause": ST.paused = True; self.send("⏸")
        elif cmd == "/resume": ST.paused = False; self.send("▶️")
        elif cmd == "/close_all":
            with ST.lk:
                for p in list(ST.ps):
                    EX.create_order(p["sym"], "sell" if p["d"]==1 else "buy", p["sz"])
                ST.ps.clear()
            self.send("🔒")
        elif cmd == "/json":
            self.send(f"<pre>{DIGITAL.to_json()[:3500]}</pre>")
        else: self.send(f"❓ {cmd}\n/help")
    def start(self):
        if not self.token: return
        def loop():
            while True:
                for u in self.get_updates():
                    self.offset = u["update_id"]+1
                    msg = u.get("message") or u.get("edited_message")
                    if not msg: continue
                    uid = msg.get("from",{}).get("id"); txt = msg.get("text","")
                    if txt:
                        try: self.handle(txt, uid)
                        except Exception as e: log.error(f"[TG] {e}")
                time.sleep(0.5)
        threading.Thread(target=loop, daemon=True).start()

TG_BOT = None
def TG_NOTIFY(text):
    if TG_BOT: TG_BOT.send(text)

HELP = """<b>🤖 MotherBot — Falcon Digital→Analog</b>

<b>وضعیت:</b>
<code>/status</code> — وضعیت زنده
<code>/best</code> — بهترین کانفیگ تأییدشده
<code>/results</code> — همه نتایج بک‌تست
<code>/strategies</code> — ۳۰ استراتژی

<b>کنترل:</b>
<code>/switch M01</code> — عوض استراتژی
<code>/modes</code> — سوییچرها
<code>/bugs</code> — باگ‌های fix‌شده

<b>اطلاعات:</b>
<code>/log</code> · <code>/log OPN 20</code>
<code>/json</code> — داده دیجیتال کامل

<b>کنترل:</b>
<code>/pause</code> · <code>/resume</code> · <code>/close_all</code>
"""

# ════════════════════════════════════════════════════════════
# Self-ping + Flask
# ════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route("/health")
def health(): return "ok", 200

@app.route("/")
def root():
    return jsonify({
        "service":"MotherBot","status":"alive",
        "best":{"name":DIGITAL.best["name"],"geo":DIGITAL.best["geo_monthly"]},
        "mode":ST.mode,"strategy":RUNNER.current_sid,
        "positions":len(ST.ps),"pnl":round(ST.pnl,2),
    })

@app.route("/api/digital")
def api_digital():
    return jsonify({
        "results":VERIFIED_RESULTS,"best":BEST_CONFIG,
        "strategies":STRATEGIES,"switcher":SWITCHER_MODES,
        "bugs":KNOWN_BUGS,
    })

@app.route("/api/status")
def api_status():
    return jsonify({
        "mode":ST.mode,"strategy":RUNNER.current_sid,
        "cap":ST.cap,"pnl":ST.pnl,"dd":ST.dd(),"daily_dd":ST.daily_dd(),
        "positions":len(ST.ps),"trades":ST.td,"wins":ST.wd,
    })

def self_ping_loop():
    url = os.getenv("SELF_URL","")
    if not url: return
    time.sleep(120)
    while True:
        try: requests.get(f"{url}/health", timeout=10)
        except: pass
        time.sleep(600)

# ════════════════════════════════════════════════════════════
# Bootstrap
# ════════════════════════════════════════════════════════════
_booted = False; _boot_lk = threading.Lock()

def start_all():
    global _booted, TG_BOT
    with _boot_lk:
        if _booted: return
        _booted = True
        log.info(f"[DIGITAL] {len(VERIFIED_RESULTS)} results, {len(STRATEGIES)} strategies loaded")
        log.info(f"[DIGITAL] best: {DIGITAL.best['name']} geo={DIGITAL.best['geo_monthly']}%")
        apply_mode("BALANCED")
        threading.Thread(target=main_loop, daemon=True).start()
        threading.Thread(target=self_ping_loop, daemon=True).start()
        TG_BOT = TelegramBot(KEYS["tg_token"], KEYS["tg_chat"])
        TG_BOT.start()
        if KEYS["tg_token"]:
            TG_BOT.send(
                f"🧠 <b>MotherBot — Digital→Analog</b>\n"
                f"Best: <code>{DIGITAL.best['name']}</code>\n"
                f"Geo: <b>{DIGITAL.best['geo_monthly']}%</b>/mo | DD={DIGITAL.best['DD']}%\n"
                f"{len(STRATEGIES)} strategies loaded\n"
                f"/help")

def main():
    start_all()
    port = int(os.getenv("PORT", 8080))
    log.info(f"[LOCAL] http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

if __name__ == "__main__":
    main()
else:
    start_all()