"""ETS 적합의 디스크 캐시 + 병렬 실행. 열쇠 = 입력 계열 바이트 · 단계 수 · ets.py 내용 · 판(python·numpy·scipy·statsmodels) 해시.
열쇠가 입력 내용이라 다른 계열의 결과를 잘못 쓸 수 없다. 예측식은 forecast_v2.ets.ets_path 그대로."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

H = 12


def _versions() -> bytes:
    import scipy
    import statsmodels

    import pbd.forecast_v2.ets as E
    s = f"{sys.version_info[:3]}|{np.__version__}|{scipy.__version__}|{statsmodels.__version__}|{H}|"
    return s.encode() + Path(E.__file__).read_bytes()


def key(x: np.ndarray, ver: bytes) -> str:
    return hashlib.sha256(ver + np.ascontiguousarray(x, dtype="<f8").tobytes()).hexdigest()


def _one(x):
    from pbd.forecast_v2.ets import ets_path
    f, why = ets_path(x, H)
    return (np.full(H, np.nan) if f is None else np.asarray(f, float)), why


def _init():
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[k] = "1"


class EtsStore:
    def __init__(self, path: Path, workers: int | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path)
        self.con.execute("create table if not exists f(k text primary key, v blob, why text)")
        self.ver = _versions()
        self.workers = workers or max((os.cpu_count() or 2) - 1, 1)
        self.hits = self.fits = 0

    def _get(self, k):
        r = self.con.execute("select v, why from f where k = ?", [k]).fetchone()
        return None if r is None else (np.frombuffer(r[0], "<f8").copy(), r[1])

    def many(self, xs: list[np.ndarray], recheck: int = 0, seed: int = 0) -> list[tuple[np.ndarray, str | None]]:
        """계열 목록의 (12단계 예측, 대체 사유). recheck개의 캐시 값을 다시 적합해 대조하고 어긋나면 멈춘다"""
        ks = [key(x, self.ver) for x in xs]
        got, miss = {}, {}
        for k, x in zip(ks, xs):
            if k in got or k in miss:
                continue
            r = self._get(k)
            if r is None:
                miss[k] = x
            else:
                got[k] = r
        self.hits += len(got)
        self.fits += len(miss)
        if recheck and got:
            pos = {k: x for k, x in zip(ks, xs)}
            pick = np.random.default_rng(seed).choice(sorted(got), min(recheck, len(got)), replace=False)
            for k in pick:
                f, why = _one(pos[k])
                if why != got[k][1] or not np.allclose(f, got[k][0], rtol=1e-9, atol=0, equal_nan=True):
                    raise RuntimeError("ETS 캐시 값이 다시 적합한 값과 다르다")
        if miss:
            _init()
            mk = list(miss)
            with ProcessPoolExecutor(self.workers, initializer=_init) as ex:
                for n, (k, r) in enumerate(zip(mk, ex.map(_one, [miss[k] for k in mk], chunksize=8))):
                    got[k] = r
                    self.con.execute("insert or replace into f values (?, ?, ?)", [k, r[0].astype("<f8").tobytes(), r[1]])
                    if n % 500 == 499:
                        self.con.commit()     # 중간에 멈춰도 이어서 한다
            self.con.commit()
        return [got[k] for k in ks]
