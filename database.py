"""Camada de dados (SQLite) do ThzyxBoTS.

Guarda economia, níveis/XP, advertências, AFK, prefixos por grupo,
cargos (bot-level), banlist, lembretes, sugestões e denúncias.
"""
import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_conn = None


def init(path: str = None):
    global _conn
    path = path or config.DATA_DB
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    cur = _conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS economy(
            jid TEXT PRIMARY KEY, balance INTEGER DEFAULT 0, last_daily INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS levels(
            jid TEXT PRIMARY KEY, xp INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS warns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat TEXT, jid TEXT, reason TEXT, by TEXT, ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS afk(
            jid TEXT PRIMARY KEY, reason TEXT, ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS prefixes(
            chat TEXT PRIMARY KEY, prefix TEXT
        );
        CREATE TABLE IF NOT EXISTS roles(
            chat TEXT, jid TEXT, role TEXT,
            PRIMARY KEY(chat, jid, role)
        );
        CREATE TABLE IF NOT EXISTS banlist(
            chat TEXT, jid TEXT, PRIMARY KEY(chat, jid)
        );
        CREATE TABLE IF NOT EXISTS reminders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat TEXT, jid TEXT, text TEXT, due INTEGER, done INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS suggestions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat TEXT, jid TEXT, text TEXT, ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat TEXT, by TEXT, target TEXT, reason TEXT, ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS settings(
            chat TEXT, key TEXT, value TEXT,
            PRIMARY KEY(chat, key)
        );
        CREATE TABLE IF NOT EXISTS whitelist(
            chat TEXT, jid TEXT, PRIMARY KEY(chat, jid)
        );
        CREATE TABLE IF NOT EXISTS auditlog(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat TEXT, actor TEXT, action TEXT, detail TEXT, ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS backups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat TEXT, data TEXT, ts INTEGER
        );
        """
    )
    _conn.commit()
    return _conn


def _exec(query, params=(), fetch=None):
    with _lock:
        cur = _conn.execute(query, params)
        if fetch == "one":
            row = cur.fetchone()
            _conn.commit()
            return row
        if fetch == "all":
            rows = cur.fetchall()
            _conn.commit()
            return rows
        _conn.commit()
        return cur.lastrowid


# ---------- economia ----------
def _ensure_economy(jid):
    if not _exec("SELECT jid FROM economy WHERE jid=?", (jid,), "one"):
        _exec("INSERT INTO economy(jid, balance) VALUES(?,?)", (jid, config.START_BALANCE))


def get_balance(jid):
    _ensure_economy(jid)
    return _exec("SELECT balance FROM economy WHERE jid=?", (jid,), "one")["balance"]


def add_balance(jid, amount):
    _ensure_economy(jid)
    _exec("UPDATE economy SET balance=balance+? WHERE jid=?", (amount, jid))
    return get_balance(jid)


def transfer(from_jid, to_jid, amount):
    if amount <= 0:
        return False, "Valor inválido."
    if get_balance(from_jid) < amount:
        return False, "Saldo insuficiente."
    add_balance(from_jid, -amount)
    add_balance(to_jid, amount)
    return True, "ok"


def claim_daily(jid):
    _ensure_economy(jid)
    row = _exec("SELECT last_daily FROM economy WHERE jid=?", (jid,), "one")
    now = int(time.time())
    if now - row["last_daily"] < 86400:
        restante = 86400 - (now - row["last_daily"])
        return False, restante
    _exec("UPDATE economy SET balance=balance+?, last_daily=? WHERE jid=?",
          (config.DAILY_REWARD, now, jid))
    return True, config.DAILY_REWARD


# ---------- níveis / XP ----------
def add_xp(jid, amount=config.XP_PER_MESSAGE):
    if not _exec("SELECT jid FROM levels WHERE jid=?", (jid,), "one"):
        _exec("INSERT INTO levels(jid, xp) VALUES(?,?)", (jid, 0))
    _exec("UPDATE levels SET xp=xp+? WHERE jid=?", (amount, jid))


def get_xp(jid):
    row = _exec("SELECT xp FROM levels WHERE jid=?", (jid,), "one")
    return row["xp"] if row else 0


def level_from_xp(xp):
    """Nível = floor(sqrt(xp/100)). Retorna (nivel, xp_no_nivel, xp_para_proximo)."""
    level = int((xp / 100) ** 0.5)
    cur_base = (level ** 2) * 100
    next_base = ((level + 1) ** 2) * 100
    return level, xp - cur_base, next_base - cur_base


def leaderboard(limit=10):
    return _exec("SELECT jid, xp FROM levels ORDER BY xp DESC LIMIT ?", (limit,), "all")


# ---------- advertências ----------
def add_warn(chat, jid, reason, by):
    _exec("INSERT INTO warns(chat, jid, reason, by, ts) VALUES(?,?,?,?,?)",
          (chat, jid, reason, by, int(time.time())))
    return get_warns_count(chat, jid)


def get_warns(chat, jid):
    return _exec("SELECT reason, by, ts FROM warns WHERE chat=? AND jid=? ORDER BY ts",
                 (chat, jid), "all")


def get_warns_count(chat, jid):
    return _exec("SELECT COUNT(*) c FROM warns WHERE chat=? AND jid=?",
                 (chat, jid), "one")["c"]


# ---------- AFK ----------
def set_afk(jid, reason):
    _exec("INSERT OR REPLACE INTO afk(jid, reason, ts) VALUES(?,?,?)",
          (jid, reason, int(time.time())))


def get_afk(jid):
    return _exec("SELECT reason, ts FROM afk WHERE jid=?", (jid,), "one")


def clear_afk(jid):
    _exec("DELETE FROM afk WHERE jid=?", (jid,))


# ---------- prefixo ----------
def get_prefix(chat):
    row = _exec("SELECT prefix FROM prefixes WHERE chat=?", (chat,), "one")
    return row["prefix"] if row else config.DEFAULT_PREFIX


def set_prefix(chat, prefix):
    _exec("INSERT OR REPLACE INTO prefixes(chat, prefix) VALUES(?,?)", (chat, prefix))


# ---------- cargos (bot-level) ----------
def add_role(chat, jid, role):
    _exec("INSERT OR IGNORE INTO roles(chat, jid, role) VALUES(?,?,?)", (chat, jid, role))


def remove_role(chat, jid, role):
    _exec("DELETE FROM roles WHERE chat=? AND jid=? AND role=?", (chat, jid, role))


def get_roles(chat, jid):
    return [r["role"] for r in
            _exec("SELECT role FROM roles WHERE chat=? AND jid=?", (chat, jid), "all")]


# ---------- banlist ----------
def add_ban(chat, jid):
    _exec("INSERT OR IGNORE INTO banlist(chat, jid) VALUES(?,?)", (chat, jid))


def is_banned(chat, jid):
    return bool(_exec("SELECT 1 FROM banlist WHERE chat=? AND jid=?", (chat, jid), "one"))


def remove_ban(chat, jid):
    _exec("DELETE FROM banlist WHERE chat=? AND jid=?", (chat, jid))


# ---------- configurações por grupo (ex.: bem-vindo, anti-*, manutenção) ----------
def set_setting(chat, key, value):
    _exec("INSERT OR REPLACE INTO settings(chat, key, value) VALUES(?,?,?)",
          (chat, key, str(value)))


def get_setting(chat, key, default=None):
    row = _exec("SELECT value FROM settings WHERE chat=? AND key=?", (chat, key), "one")
    return row["value"] if row else default


# ---------- whitelist (isenta da automoderação) ----------
def whitelist_add(chat, jid):
    _exec("INSERT OR IGNORE INTO whitelist(chat, jid) VALUES(?,?)", (chat, jid))


def whitelist_remove(chat, jid):
    _exec("DELETE FROM whitelist WHERE chat=? AND jid=?", (chat, jid))


def is_whitelisted(chat, jid):
    return bool(_exec("SELECT 1 FROM whitelist WHERE chat=? AND jid=?", (chat, jid), "one"))


# ---------- audit log ----------
def log_action(chat, actor, action, detail=""):
    return _exec("INSERT INTO auditlog(chat, actor, action, detail, ts) VALUES(?,?,?,?,?)",
                 (chat, actor, action, detail, int(time.time())))


def get_auditlog(chat, limit=10):
    return _exec("SELECT actor, action, detail, ts FROM auditlog WHERE chat=? "
                 "ORDER BY id DESC LIMIT ?", (chat, limit), "all")


# ---------- backups ----------
def backup_save(chat, data):
    return _exec("INSERT INTO backups(chat, data, ts) VALUES(?,?,?)",
                 (chat, data, int(time.time())))


def backup_get(chat, bid):
    return _exec("SELECT data, ts FROM backups WHERE chat=? AND id=?", (chat, bid), "one")


# ---------- lembretes ----------
def add_reminder(chat, jid, text, due):
    return _exec("INSERT INTO reminders(chat, jid, text, due) VALUES(?,?,?,?)",
                 (chat, jid, text, due))


def due_reminders(now):
    return _exec("SELECT id, chat, jid, text FROM reminders WHERE done=0 AND due<=?",
                 (now,), "all")


def mark_reminder_done(rid):
    _exec("UPDATE reminders SET done=1 WHERE id=?", (rid,))


# ---------- sugestões / denúncias ----------
def add_suggestion(chat, jid, text):
    return _exec("INSERT INTO suggestions(chat, jid, text, ts) VALUES(?,?,?,?)",
                 (chat, jid, text, int(time.time())))


def add_report(chat, by, target, reason):
    return _exec("INSERT INTO reports(chat, by, target, reason, ts) VALUES(?,?,?,?,?)",
                 (chat, by, target, reason, int(time.time())))
