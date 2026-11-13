"""Store dated signal samples and settle their five-session outcomes."""
import csv
import io

from stockai.data import recommendation

HORIZON = 5
MIN_SAMPLES = 10
RULE = "technical-v1"


def create_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS signal_samples (
        mode TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL, rule TEXT NOT NULL,
        signal_date TEXT NOT NULL, signal TEXT NOT NULL, score INTEGER NOT NULL,
        basis_close REAL NOT NULL, source TEXT NOT NULL, outcome_date TEXT,
        outcome_close REAL, forward_return REAL, hit INTEGER, evaluation_basis_close REAL,
        PRIMARY KEY (mode, market, symbol, rule, signal_date)
    )""")


def record_and_score(db, mode, item, bars, current):
    key = (mode, item["market"], item["symbol"], RULE)
    dates = [day.date().isoformat() for day in bars.index]
    positions = {day: i for i, day in enumerate(dates)}
    last = db.execute("""SELECT MAX(signal_date) FROM signal_samples
                         WHERE mode=? AND market=? AND symbol=? AND rule=?""", key).fetchone()[0]
    for i in range(54, len(bars)):
        if last and dates[i] <= last:
            continue
        latest = i == len(bars) - 1
        signal = current if latest else recommendation(bars.iloc[:i + 1])
        db.execute("""INSERT OR IGNORE INTO signal_samples
            (mode, market, symbol, rule, signal_date, signal, score, basis_close, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*key, dates[i], signal["label"], signal["score"], float(bars.close.iloc[i]),
             "observed" if latest else "backfill"))

    pending = db.execute("""SELECT signal_date, signal FROM signal_samples
        WHERE mode=? AND market=? AND symbol=? AND rule=? AND hit IS NULL AND signal != '중립·관망'""", key)
    for day, signal in pending.fetchall():
        if day not in positions or positions[day] + HORIZON >= len(bars):
            continue
        i = positions[day]
        j = i + HORIZON
        # Compare prices from the same adjusted series, even after a split.
        basis = float(bars.close.iloc[i])
        change = float(bars.close.iloc[j] / basis - 1)
        hit = int(change > 0 if signal == "관심 후보" else change < 0)
        db.execute("""UPDATE signal_samples SET outcome_date=?, outcome_close=?, forward_return=?, hit=?,
                      evaluation_basis_close=?
                      WHERE mode=? AND market=? AND symbol=? AND rule=? AND signal_date=? AND hit IS NULL""",
                   (dates[j], float(bars.close.iloc[j]), change, hit, basis, *key, day))

    if current["label"] == "중립·관망":
        return None
    rows = db.execute("""SELECT signal_date, outcome_date, hit FROM signal_samples
        WHERE mode=? AND market=? AND symbol=? AND rule=? AND signal=? AND outcome_date<=?
        ORDER BY signal_date""", (*key, current["label"], dates[-1]))
    samples = hits = 0
    available = ""
    for day, outcome, hit in rows:
        if day >= available:
            available = outcome
            samples += 1
            hits += hit
    return {"samples": samples, "hits": hits, "horizon": HORIZON,
            "percent": round(100 * hits / samples) if samples >= MIN_SAMPLES else None}


def export_csv(db, mode):
    rows = db.execute("""SELECT * FROM signal_samples WHERE mode=?
                          ORDER BY market, symbol, signal_date""", (mode,))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([column[0] for column in rows.description])
    writer.writerows(rows)
    return "\ufeff" + output.getvalue()
