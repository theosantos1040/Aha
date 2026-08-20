"""Camada de dados (SQLite) do ThzyxBoTS.

Guarda economia (carteira/banco), níveis/XP, advertências, AFK, prefixos por
grupo, cargos (bot-level), banlist, blacklist de expressões, lembretes,
sugestões, denúncias, inventário/loja, casamentos, desafio diário,
atividade por grupo e pacotes de figurinha.
"""
import contextlib
import sqlite3
import threading
import time

import config

_lock = threading.RLock()
_conn = None

# Maior valor que cabe no INTEGER de 64 bits do SQLite. Um id/duração digitado
# por engano com dezenas de dígitos (ex.: "/backup-load 99999999999999999999")
# faz o driver sqlite3 levantar OverflowError na hora de fazer o bind do
# parâmetro — _exec() converte isso numa exceção comum abaixo, então quem
# valida esse tipo de entrada nos comandos também pode reaproveitar esta
# constante para recusar o valor ANTES de gastar uma consulta.
SQLITE_MAX_INT = 2 ** 63 - 1

# Catálogo padrão da loja (/loja e /comprar). Só é inserido se ainda não existir.
SHOP_DEFAULT = [
    ("cafe", "Café", 50, "Restaura o ânimo para trabalhar."),
    ("pizza", "Pizza", 120, "Uma fatia sempre cai bem."),
    ("rosa", "Rosa", 200, "Perfeita para um pedido de casamento."),
    ("anel", "Anel", 1500, "Símbolo de compromisso eterno."),
    ("escudo", "Escudo", 800, "Dizem que espanta ladrões."),
    ("trofeu", "Troféu", 3000, "Só para quem gosta de ostentar."),
]


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
        CREATE TABLE IF NOT EXISTS bank(
            jid TEXT PRIMARY KEY, amount INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS cooldowns(
            jid TEXT, kind TEXT, ts INTEGER,
            PRIMARY KEY(jid, kind)
        );
        CREATE TABLE IF NOT EXISTS shop(
            item_id TEXT PRIMARY KEY, name TEXT, price INTEGER, description TEXT
        );
        CREATE TABLE IF NOT EXISTS inventory(
            jid TEXT, item_id TEXT, quantity INTEGER DEFAULT 0,
            PRIMARY KEY(jid, item_id)
        );
        CREATE TABLE IF NOT EXISTS marriages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat TEXT, jid1 TEXT, jid2 TEXT, status TEXT, ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS daily_challenges(
            jid TEXT, day TEXT, challenge TEXT, reward INTEGER,
            claimed INTEGER DEFAULT 0, ts INTEGER,
            PRIMARY KEY(jid, day)
        );
        CREATE TABLE IF NOT EXISTS blacklist(
            chat TEXT, term TEXT, term_norm TEXT, by TEXT, ts INTEGER,
            PRIMARY KEY(chat, term_norm)
        );
        CREATE TABLE IF NOT EXISTS activity(
            chat TEXT, jid TEXT, ts INTEGER,
            PRIMARY KEY(chat, jid)
        );
        CREATE TABLE IF NOT EXISTS sticker_packs(
            scope TEXT, owner TEXT, name TEXT, author TEXT,
            PRIMARY KEY(scope, owner)
        );
        """
    )
    cur.executemany(
        "INSERT OR IGNORE INTO shop(item_id, name, price, description) VALUES(?,?,?,?)",
        SHOP_DEFAULT,
    )
    _conn.commit()
    # autocommit: cada _exec já grava na hora e o _tx controla BEGIN/COMMIT.
    _conn.isolation_level = None
    return _conn


def _exec(query, params=(), fetch=None):
    with _lock:
        try:
            cur = _conn.execute(query, params)
        except OverflowError as exc:
            # Um parâmetro inteiro maior que SQLITE_MAX_INT (ex.: um id ou
            # duração digitado com dezenas de dígitos) faz o driver sqlite3
            # levantar OverflowError crua na hora do bind — sem isso, ela
            # vazava até o usuário via o catch-all genérico de
            # handle_command como "Python int too large to convert to SQLite
            # INTEGER". Convertida aqui, uma vez só, para qualquer chamador.
            raise ValueError("número muito grande — verifique o valor informado.") from exc
        if fetch == "one":
            row = cur.fetchone()
            _conn.commit()
            return row
        if fetch == "all":
            rows = cur.fetchall()
            _conn.commit()
            return rows
        if fetch == "count":
            # nº de linhas afetadas (útil em DELETE/UPDATE que devolvem bool)
            count = cur.rowcount
            _conn.commit()
            return count
        _conn.commit()
        return cur.lastrowid


@contextlib.contextmanager
def _tx():
    """Transação atômica (BEGIN IMMEDIATE) para SELECT+UPDATE sem corrida.

    Usada na economia (roubo/aposta/banco/loja) e no casamento, onde duas
    chamadas simultâneas não podem deixar o saldo negativo nem gerar bigamia.
    """
    with _lock:
        cur = _conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            yield cur
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        cur.execute("COMMIT")


def _to_int(value):
    """Converte para int; devolve None se não der."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def scores_for_phones(phones, kind="xp"):
    """Pontuação (xp ou moedas) de uma lista de telefones. -> [(telefone, valor)]

    Casa pelo NÚMERO, não pelo JID inteiro. O JID gravado pode carregar o
    device (`5511999999999:12@s.whatsapp.net`), enquanto a lista de
    participantes do grupo vem sem ele. Comparar o JID cru daria zero para
    todo mundo — e consultar com um JID sintetizado era pior ainda, porque
    `_ensure_economy` criava uma linha nova para cada participante a cada
    vez que alguém chamasse o ranking.
    """
    alvo = {str(p) for p in phones}
    if not alvo:
        return []
    tabela, coluna = ("levels", "xp") if kind == "xp" else ("economy", "balance")
    somas = {p: 0 for p in alvo}
    for row in _exec(f"SELECT jid, {coluna} AS v FROM {tabela}", (), "all") or []:
        # jid -> só o número, sem device nem servidor
        numero = str(row["jid"]).split("@")[0].split(":")[0]
        if numero in somas:
            somas[numero] = max(somas[numero], row["v"] or 0)
    return [(p, somas[p]) for p in alvo]


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


# ---------- carteira + banco ----------
def _ensure_wallet(cur, jid):
    """Garante linhas de carteira e banco dentro de uma transação."""
    cur.execute("INSERT OR IGNORE INTO economy(jid, balance) VALUES(?,?)",
                (jid, config.START_BALANCE))
    cur.execute("INSERT OR IGNORE INTO bank(jid, amount) VALUES(?,0)", (jid,))


def _wallet_of(cur, jid):
    return cur.execute("SELECT balance FROM economy WHERE jid=?", (jid,)).fetchone()["balance"]


def _bank_of(cur, jid):
    return cur.execute("SELECT amount FROM bank WHERE jid=?", (jid,)).fetchone()["amount"]


def get_wallet_and_bank(jid):
    """Devolve a tupla (carteira, banco) do usuário."""
    with _tx() as cur:
        _ensure_wallet(cur, jid)
        return _wallet_of(cur, jid), _bank_of(cur, jid)


def _move_money(jid, amount, to_bank):
    """Move moedas entre carteira e banco sem deixar nenhum lado negativo."""
    amount = _to_int(amount)
    if amount is None or amount <= 0:
        return False, "Valor inválido."
    with _tx() as cur:
        _ensure_wallet(cur, jid)
        wallet, bank = _wallet_of(cur, jid), _bank_of(cur, jid)
        if to_bank and wallet < amount:
            return False, "Saldo insuficiente na carteira."
        if not to_bank and bank < amount:
            return False, "Saldo insuficiente no banco."
        delta = -amount if to_bank else amount
        cur.execute("UPDATE economy SET balance=balance+? WHERE jid=?", (delta, jid))
        cur.execute("UPDATE bank SET amount=amount-? WHERE jid=?", (delta, jid))
        return True, {"balance": wallet + delta, "bank": bank - delta}


def deposit(jid, amount):
    """Guarda moedas no banco. Devolve (ok, {'balance','bank'}) ou (False, motivo)."""
    return _move_money(jid, amount, to_bank=True)


def withdraw(jid, amount):
    """Saca moedas do banco. Devolve (ok, {'balance','bank'}) ou (False, motivo)."""
    return _move_money(jid, amount, to_bank=False)


# ---------- cooldowns (trabalhar, roubar, desafio) ----------
def _cooldown_left(cur, jid, kind, cooldown, now):
    """Segundos que ainda faltam para o cooldown acabar (0 = liberado)."""
    row = cur.execute("SELECT ts FROM cooldowns WHERE jid=? AND kind=?",
                      (jid, kind)).fetchone()
    if row and now - row["ts"] < cooldown:
        return cooldown - (now - row["ts"])
    return 0


def _cooldown_set(cur, jid, kind, now):
    cur.execute("INSERT OR REPLACE INTO cooldowns(jid, kind, ts) VALUES(?,?,?)",
                (jid, kind, now))


def claim_work(jid, reward=100, cooldown=3600):
    """Trabalha e ganha moedas. (True, recompensa) ou (False, segundos restantes)."""
    reward = max(0, _to_int(reward) or 0)
    now = int(time.time())
    with _tx() as cur:
        _ensure_wallet(cur, jid)
        left = _cooldown_left(cur, jid, "work", cooldown, now)
        if left:
            return False, left
        cur.execute("UPDATE economy SET balance=balance+? WHERE jid=?", (reward, jid))
        _cooldown_set(cur, jid, "work", now)
        return True, reward


# ---------- roubo / aposta ----------
def rob_user(jid, target, amount, success=True, penalty=1, cooldown=1800):
    """Tenta roubar alguém.

    Devolve (True, {'success', 'amount'}), (False, segundos) quando em
    cooldown, ou (False, motivo) quando a tentativa é inválida.
    amount e penalty precisam ser positivos.
    """
    amount, penalty = _to_int(amount), _to_int(penalty)
    if amount is None or amount <= 0 or penalty is None or penalty <= 0:
        # valor negativo inverteria o sentido da transferência e criaria moedas
        return False, "Valor inválido."
    if not target:
        return False, "Alvo inválido."
    if jid == target:
        return False, "Você não pode roubar de si mesmo."
    now = int(time.time())
    with _tx() as cur:
        _ensure_wallet(cur, jid)
        _ensure_wallet(cur, target)
        left = _cooldown_left(cur, jid, "rob", cooldown, now)
        if left:
            return False, left
        ladrao, vitima = _wallet_of(cur, jid), _wallet_of(cur, target)
        if success and vitima <= 0:
            return False, "O alvo está duro, não há o que roubar."
        # nunca deixa nenhum dos dois com saldo negativo
        movido = min(amount, vitima) if success else min(penalty, ladrao)
        if success:
            cur.execute("UPDATE economy SET balance=balance-? WHERE jid=?", (movido, target))
            cur.execute("UPDATE economy SET balance=balance+? WHERE jid=?", (movido, jid))
        else:
            cur.execute("UPDATE economy SET balance=balance-? WHERE jid=?", (movido, jid))
        _cooldown_set(cur, jid, "rob", now)
        return True, {"success": bool(success), "amount": movido}


def place_bet(jid, amount, won, multiplier=2):
    """Aposta moedas. (True, {'won','stake','payout','balance'}) ou (False, motivo)."""
    amount = _to_int(amount)
    if amount is None or amount <= 0:
        return False, "Valor inválido."
    with _tx() as cur:
        _ensure_wallet(cur, jid)
        saldo = _wallet_of(cur, jid)
        if saldo < amount:
            return False, "Saldo insuficiente para essa aposta."
        payout = int(amount * multiplier) if won else 0
        delta = payout - amount
        cur.execute("UPDATE economy SET balance=balance+? WHERE jid=?", (delta, jid))
        return True, {"won": bool(won), "stake": amount,
                      "payout": payout, "balance": saldo + delta}


# ---------- loja / inventário ----------
def shop_list():
    """Itens à venda (item_id, name, price, description)."""
    return _exec("SELECT item_id, name, price, description FROM shop ORDER BY price", (), "all")


def get_inventory(jid):
    """Itens do usuário (item_id, name, quantity), sem os zerados."""
    return _exec(
        "SELECT i.item_id AS item_id, s.name AS name, i.quantity AS quantity "
        "FROM inventory i LEFT JOIN shop s ON s.item_id=i.item_id "
        "WHERE i.jid=? AND i.quantity>0 ORDER BY i.item_id", (jid,), "all")


def buy_item(jid, item_id, quantity=1):
    """Compra itens da loja.

    (True, {'item_id','name','quantity','total','balance'}) ou (False, motivo).
    """
    quantity = _to_int(quantity)
    if quantity is None or quantity <= 0:
        return False, "Quantidade inválida."
    item_id = (item_id or "").strip().casefold()
    with _tx() as cur:
        _ensure_wallet(cur, jid)
        item = cur.execute("SELECT item_id, name, price FROM shop WHERE item_id=?",
                           (item_id,)).fetchone()
        if not item:
            return False, "Item não encontrado. Veja /loja."
        total = item["price"] * quantity
        saldo = _wallet_of(cur, jid)
        if saldo < total:
            return False, f"Saldo insuficiente: precisa de {total} moedas."
        cur.execute("UPDATE economy SET balance=balance-? WHERE jid=?", (total, jid))
        cur.execute("INSERT INTO inventory(jid, item_id, quantity) VALUES(?,?,?) "
                    "ON CONFLICT(jid, item_id) DO UPDATE SET quantity=quantity+?",
                    (jid, item["item_id"], quantity, quantity))
        return True, {"item_id": item["item_id"], "name": item["name"],
                      "quantity": quantity, "total": total, "balance": saldo - total}


# ---------- casamento ----------
# Regra: o pedido é por grupo, mas o casamento em si é único por pessoa —
# quem já está casado (em qualquer chat) não pode casar de novo.
def _active_marriage(cur, jid):
    return cur.execute(
        "SELECT * FROM marriages WHERE status='casado' AND (jid1=? OR jid2=?) LIMIT 1",
        (jid, jid)).fetchone()


def _pending_marriage(cur, chat, jid):
    return cur.execute(
        "SELECT * FROM marriages WHERE chat=? AND status='pendente' AND (jid1=? OR jid2=?) "
        "ORDER BY id DESC LIMIT 1", (chat, jid, jid)).fetchone()


def propose_marriage(chat, jid, target):
    """Pede alguém em casamento. (True, pedido) ou (False, motivo)."""
    if not target:
        return False, "Marque quem você quer pedir em casamento."
    if jid == target:
        return False, "Você não pode se casar consigo mesmo."
    now = int(time.time())
    with _tx() as cur:
        if _active_marriage(cur, jid):
            return False, "Você já está casado(a). Use /casar divorciar primeiro."
        if _active_marriage(cur, target):
            return False, "Essa pessoa já está casada."
        if _pending_marriage(cur, chat, jid):
            return False, "Você já tem um pedido de casamento pendente."
        if _pending_marriage(cur, chat, target):
            return False, "Essa pessoa já tem um pedido pendente."
        cur.execute("INSERT INTO marriages(chat, jid1, jid2, status, ts) "
                    "VALUES(?,?,?,'pendente',?)", (chat, jid, target, now))
        return True, {"chat": chat, "jid1": jid, "jid2": target, "status": "pendente"}


def accept_marriage(chat, jid):
    """Aceita o pedido feito para você. (True, {'jid1','jid2','status'}) ou (False, motivo)."""
    now = int(time.time())
    with _tx() as cur:
        row = cur.execute(
            "SELECT * FROM marriages WHERE chat=? AND status='pendente' AND jid2=? "
            "ORDER BY id DESC LIMIT 1", (chat, jid)).fetchone()
        if not row:
            return False, "Nenhum pedido de casamento pendente para você."
        if _active_marriage(cur, jid):
            return False, "Você já está casado(a)."
        if _active_marriage(cur, row["jid1"]):
            cur.execute("DELETE FROM marriages WHERE id=?", (row["id"],))
            return False, "Quem te pediu já se casou com outra pessoa."
        cur.execute("UPDATE marriages SET status='casado', ts=? WHERE id=?", (now, row["id"]))
        return True, {"jid1": row["jid1"], "jid2": row["jid2"], "status": "casado"}


def decline_marriage(chat, jid):
    """Recusa (ou cancela) o pedido pendente. True se havia algum."""
    return _exec("DELETE FROM marriages WHERE chat=? AND status='pendente' "
                 "AND (jid1=? OR jid2=?)", (chat, jid, jid), "count") > 0


def divorce(chat, jid):
    """Desfaz o casamento ativo da pessoa. True se estava casada."""
    return _exec("DELETE FROM marriages WHERE status='casado' AND (jid1=? OR jid2=?)",
                 (jid, jid), "count") > 0


def get_marriage(chat, jid, include_pending=False):
    """Casamento ativo da pessoa; com include_pending, também o pedido do chat."""
    with _tx() as cur:
        row = _active_marriage(cur, jid)
        if row or not include_pending:
            return row
        return _pending_marriage(cur, chat, jid)


# ---------- desafio diário ----------
def get_daily_challenge(jid, day, challenge, reward):
    """Cria (se preciso) e devolve o desafio do dia: challenge, reward, claimed."""
    _exec("INSERT OR IGNORE INTO daily_challenges(jid, day, challenge, reward, claimed, ts) "
          "VALUES(?,?,?,?,0,?)", (jid, day, challenge, reward, int(time.time())))
    return _exec("SELECT day, challenge, reward, claimed FROM daily_challenges "
                 "WHERE jid=? AND day=?", (jid, day), "one")


def claim_daily_challenge(jid, reward, challenge, day):
    """Resgata a recompensa do desafio do dia. (True, moedas) ou (False, motivo)."""
    now = int(time.time())
    with _tx() as cur:
        _ensure_wallet(cur, jid)
        cur.execute("INSERT OR IGNORE INTO daily_challenges"
                    "(jid, day, challenge, reward, claimed, ts) VALUES(?,?,?,?,0,?)",
                    (jid, day, challenge, reward, now))
        row = cur.execute("SELECT reward, claimed FROM daily_challenges WHERE jid=? AND day=?",
                          (jid, day)).fetchone()
        if row["claimed"]:
            return False, "Você já resgatou o desafio de hoje."
        premio = max(0, _to_int(row["reward"]) or 0)
        cur.execute("UPDATE daily_challenges SET claimed=1, ts=? WHERE jid=? AND day=?",
                    (now, jid, day))
        cur.execute("UPDATE economy SET balance=balance+? WHERE jid=?", (premio, jid))
        return True, premio


# ---------- blacklist de expressões ----------
def _norm_term(term):
    return " ".join((term or "").split()).casefold()


def blacklist_add(chat, term, by=""):
    """Adiciona expressão proibida. False se já existia."""
    norm = _norm_term(term)
    if not norm:
        return False
    return _exec("INSERT OR IGNORE INTO blacklist(chat, term, term_norm, by, ts) "
                 "VALUES(?,?,?,?,?)",
                 (chat, term.strip(), norm, by, int(time.time())), "count") > 0


def blacklist_remove(chat, term):
    """Remove expressão proibida. False se não existia."""
    return _exec("DELETE FROM blacklist WHERE chat=? AND term_norm=?",
                 (chat, _norm_term(term)), "count") > 0


def blacklist_list(chat):
    """Lista de expressões proibidas do grupo (strings)."""
    return [r["term"] for r in
            _exec("SELECT term FROM blacklist WHERE chat=? ORDER BY term", (chat,), "all")]


def find_blacklisted(chat, text):
    """Devolve a primeira expressão proibida achada no texto (ou None).

    Casa por PALAVRA INTEIRA, não por pedaço. Com substring solta, banir "pix"
    apagava qualquer mensagem com "pixel"; banir "ola" pegava "escola". Termos
    com espaço ("compre agora") continuam funcionando, porque a fronteira é
    verificada só nas pontas da expressão.
    """
    if not text:
        return None
    alvo = _norm_term(text)
    for row in _exec("SELECT term, term_norm FROM blacklist WHERE chat=?", (chat,), "all"):
        termo = row["term_norm"]
        if not termo:
            continue
        inicio = 0
        while True:
            pos = alvo.find(termo, inicio)
            if pos < 0:
                break
            antes = alvo[pos - 1] if pos > 0 else " "
            fim = pos + len(termo)
            depois = alvo[fim] if fim < len(alvo) else " "
            if not antes.isalnum() and not depois.isalnum():
                return row["term"]
            inicio = pos + 1
    return None


# ---------- listagens de moderação ----------
def list_muted(chat):
    """JIDs silenciados no grupo (cargo 'muted')."""
    return [r["jid"] for r in
            _exec("SELECT jid FROM roles WHERE chat=? AND role='muted' ORDER BY jid",
                  (chat,), "all")]


def list_banned(chat):
    """JIDs na banlist do grupo."""
    return [r["jid"] for r in
            _exec("SELECT jid FROM banlist WHERE chat=? ORDER BY jid", (chat,), "all")]


def delete_warn(chat, jid, warn_id=None):
    """Apaga uma advertência pela POSIÇÃO mostrada no /checkwarns (1..N).

    Sem argumento, apaga a mais recente.

    A numeração é sempre a da lista, nunca o `id` da tabela. Antes tentava o
    `id` primeiro e só caía para a posição se não achasse: como os ids são
    globais e crescentes, `/delwarn @fulano 2` podia casar com o id 2 — que
    costuma ser a advertência nº 1 dessa pessoa — e apagava a errada.
    """
    if warn_id is None:
        return _exec("DELETE FROM warns WHERE id=(SELECT id FROM warns WHERE chat=? AND jid=? "
                     "ORDER BY id DESC LIMIT 1)", (chat, jid), "count") > 0
    rows = _exec("SELECT id FROM warns WHERE chat=? AND jid=? ORDER BY id", (chat, jid), "all")
    if not 1 <= warn_id <= len(rows):
        return False
    return _exec("DELETE FROM warns WHERE id=?", (rows[warn_id - 1]["id"],), "count") > 0


# ---------- lembretes do usuário ----------
def list_reminders(jid, chat):
    """Lembretes ativos do usuário no chat (id, text, due)."""
    return _exec("SELECT id, text, due FROM reminders WHERE jid=? AND chat=? AND done=0 "
                 "ORDER BY due", (jid, chat), "all")


def cancel_reminder(rid, jid, chat):
    """Cancela um lembrete ativo do próprio usuário. True se cancelou."""
    return _exec("UPDATE reminders SET done=1 WHERE id=? AND jid=? AND chat=? AND done=0",
                 (rid, jid, chat), "count") > 0


# ---------- atividade por grupo (/inativos) ----------
def record_activity(chat, jid):
    """Marca agora como última atividade da pessoa no grupo."""
    _exec("INSERT OR REPLACE INTO activity(chat, jid, ts) VALUES(?,?,?)",
          (chat, jid, int(time.time())))


def get_last_activity(chat, jid):
    """Epoch da última mensagem da pessoa no grupo, ou None se nunca falou."""
    row = _exec("SELECT ts FROM activity WHERE chat=? AND jid=?", (chat, jid), "one")
    return row["ts"] if row else None


# ---------- pacote de figurinhas ----------
def set_sticker_pack(owner, name, author, scope="user"):
    """Define o pacote/autor das figurinhas (scope 'user' ou 'chat')."""
    _exec("INSERT OR REPLACE INTO sticker_packs(scope, owner, name, author) VALUES(?,?,?,?)",
          (scope, owner, name, author))


def get_sticker_pack(owner, scope="user"):
    return _exec("SELECT name, author FROM sticker_packs WHERE scope=? AND owner=?",
                 (scope, owner), "one")


def resolve_sticker_pack(jid, chat, default_name, default_author):
    """Pacote a usar: preferência do usuário, depois do grupo, depois o padrão."""
    for scope, owner in (("user", jid), ("chat", chat)):
        row = get_sticker_pack(owner, scope)
        if row and row["name"]:
            return row["name"], row["author"] or default_author
    return default_name, default_author
