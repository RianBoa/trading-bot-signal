# core/m1/model.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, List
import json
import math


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    else:
        ez = math.exp(z)
        return ez / (1.0 + ez)


@dataclass
class OnlineLogReg:
    # веса по фичам (все фичи должны быть числовыми)
    w: Dict[str, float]
    b: float = 0.0
    lr: float = 0.05
    l2: float = 1e-6

    @classmethod
    def load(cls, path: Path) -> "OnlineLogReg":
        try:
            if path.exists():
                obj = json.loads(path.read_text(encoding="utf-8"))
                return cls(w=obj.get("w", {}), b=float(obj.get("b", 0.0)),
                           lr=float(obj.get("lr", 0.05)), l2=float(obj.get("l2", 1e-6)))
        except Exception:
            pass
        return cls(w={}, b=0.0)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8")

    def _dot(self, x: Dict[str, Any]) -> float:
        s = self.b
        for k, v in x.items():
            try:
                fv = float(v)
            except Exception:
                continue
            s += self.w.get(k, 0.0) * fv
        return s

    def predict_proba(self, x: Dict[str, Any]) -> float:
        z = self._dot(x)
        return _sigmoid(z)

    def fit_batch(self, X: List[Dict[str, Any]], y: List[int], epochs: int = 2) -> None:
        # простая SGD по батчу
        n = min(len(X), len(y))
        for _ in range(epochs):
            for i in range(n):
                xi = X[i]
                yi = float(y[i])
                p = self.predict_proba(xi)
                grad = (p - yi)

                # bias
                self.b -= self.lr * grad

                # weights
                for k, v in xi.items():
                    try:
                        fv = float(v)
                    except Exception:
                        continue
                    wk = self.w.get(k, 0.0)
                    wk -= self.lr * (grad * fv + self.l2 * wk)
                    self.w[k] = wk
