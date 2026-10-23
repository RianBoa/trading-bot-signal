import sqlite3
from pathlib import Path

# 1) Кандидаты путей (самый важный — тот, что в TradeJournal/telegram_bot.py)
CANDIDATES = [
    Path(r"E:\trading_bot\ai_data\trades.db"),
    Path(r"E:\trading_bot\trades.db"),
]

TABLE = "trades"
COLUMN = "narrative"
COLUMN_TYPE = "TEXT"

def list_tables(conn):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    return [r[0] for r in cur.fetchall()]

def column_exists(conn, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table});")
    return any(row[1] == col for row in cur.fetchall())  # row[1] = column name

def pick_db() -> Path:
    existing = [p for p in CANDIDATES if p.exists()]
    if not existing:
        raise FileNotFoundError(
            "Не найден trades.db. Проверенные пути:\n" + "\n".join(str(p) for p in CANDIDATES)
        )

    # выбираем тот db, где реально есть таблица trades
    for p in existing:
        try:
            conn = sqlite3.connect(p, timeout=30)
            conn.execute("PRAGMA busy_timeout=30000;")
            tables = list_tables(conn)
            conn.close()
            if TABLE in tables:
                return p
        except sqlite3.Error:
            try:
                conn.close()
            except Exception:
                pass

    # если ни в одном нет trades — покажем таблицы в каждом, чтобы понять куда пишется
    details = []
    for p in existing:
        try:
            conn = sqlite3.connect(p, timeout=5)
            tables = list_tables(conn)
            conn.close()
            details.append(f"{p} -> tables: {tables}")
        except sqlite3.Error as e:
            details.append(f"{p} -> sqlite error: {e}")

    raise RuntimeError(
        "Файлы БД найдены, но таблица 'trades' не найдена ни в одном.\n" +
        "\n".join(details) +
        "\n\nЗначит бот пишет в другую БД или еще не создал таблицы."
    )

def main():
    db_path = pick_db()
    print(f"[OK] Using DB: {db_path}")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000;")  # ждём если база занята
    try:
        tables = list_tables(conn)
        print("[OK] tables:", tables)

        if TABLE not in tables:
            raise RuntimeError(f"Table '{TABLE}' not found in {db_path}")

        if column_exists(conn, TABLE, COLUMN):
            print(f"[OK] Column {TABLE}.{COLUMN} already exists")
            return

        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_TYPE};")
        conn.commit()
        print(f"[OK] Added column {TABLE}.{COLUMN}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
