# core/m1/config.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AIConfig:
    # storage
    data_dir: Path = Path("ai_data")

    # live flags
    enabled: bool = True                 # включает AI слой (фильтрация/логирование)
    training_enabled: bool = True        # включает переобучение
    allow_block_entries: bool = True     # разрешить AI блокировать ENTER (если модель против)

    # training cadence
    min_train_samples: int = 200         # минимум сделок для первого обучения (старое имя)
    min_labeled_trades: int = 200        # ✅ алиас для ai_live.py (ожидает это имя)
    retrain_every_n_trades: int = 50     # переобучать каждые N закрытых сделок
    
    
    retrain_every_sec: int = 999999999   # ✅ алиас для ai_live.py (по сути отключает таймер)

    # label definition
    positive_outcome: str = "TP"         # 1=win если outcome == "TP"

    # model threshold
    min_prob_to_allow: float = 0.0      # если p(win) < threshold -> блокируем вход

    # features
    feature_lookback: int = 300
