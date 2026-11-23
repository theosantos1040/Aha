"""Funções utilitárias puras (testáveis sem WhatsApp)."""
import ast
import math
import operator
import re

# ---------- Calculadora segura ----------
_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


# Teto para o TAMANHO do resultado de uma potência, em bits (~30 mil dígitos
# decimais — generoso pra uma calculadora de chat). Sem isso, algo como
# "/calc 9**999999999" (qualquer usuário, sem precisar ser admin) tentava
# computar um inteiro de ~3 bilhões de dígitos: o processo travava por
# minutos consumindo toda a CPU e gigabytes de RAM. Como o bot despacha
# TODAS as mensagens de TODOS os chats numa única thread síncrona (sem pool
# de workers), essa única mensagem travava o bot inteiro, não só quem
# calculou.
MAX_CALC_RESULT_BITS = 100_000


def _checar_magnitude_potencia(base, expoente):
    """Recusa uma exponenciação ANTES de computá-la, se o resultado for
    grande demais. float**algo já satura em inf/OverflowError sozinho —
    só int**int positivo cresce sem limite (Python faz aritmética exata de
    precisão arbitrária), e é o único caso perigoso de verdade."""
    if (
        isinstance(base, int) and isinstance(expoente, int)
        and expoente > 0 and abs(base) >= 2
    ):
        bits_estimados = expoente * math.log2(abs(base))
        if bits_estimados > MAX_CALC_RESULT_BITS:
            raise ValueError("resultado grande demais para calcular")


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("valor inválido")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow):
            _checar_magnitude_potencia(left, right)
        return _ALLOWED_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("expressão não permitida")


def safe_calc(expr: str):
    """Avalia uma expressão matemática com segurança (sem eval direto)."""
    expr = expr.strip().replace("^", "**").replace(",", ".").replace("x", "*").replace("X", "*")
    if not expr:
        raise ValueError("expressão vazia")
    tree = ast.parse(expr, mode="eval")
    result = _eval_node(tree.body)
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return result


# ---------- Parsing de duração ----------
_DURATION_RE = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

# Teto de 10 anos. Sem isso, uma duração absurda (ex.: "99999999999999999999d",
# ou um número puro grande caindo no fallback "minutos" abaixo) virava um
# segundos gigantesco demais para threading.Timer/Event.wait (que só aceita
# floats num intervalo razoável — "OverflowError: timestamp too large to
# convert to C _PyTime_t") e para o INTEGER de 64 bits do SQLite. O efeito era
# bem diferente em cada comando: /timer criava um Timer que morria sozinho em
# segundo plano SEM avisar ninguém (aviso nunca chegava); /remind e /tempban
# vazavam a OverflowError crua pro usuário via o catch-all de handle_command.
MAX_DURATION_SECONDS = 3650 * 86400  # 10 anos


def parse_duration(text: str) -> int:
    """Converte '1h30m', '10m', '2d' em segundos. 0 se inválido.

    Sempre limitado a MAX_DURATION_SECONDS — ver o comentário acima.
    """
    if not text:
        return 0
    total = 0
    for amount, unit in _DURATION_RE.findall(text):
        total += int(amount) * _UNIT_SECONDS[unit.lower()]
    if total == 0 and text.strip().isdigit():
        total = int(text.strip()) * 60  # número puro = minutos
    return min(total, MAX_DURATION_SECONDS)


def human_uptime(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def progress_bar(current: int, total: int, size: int = 10) -> str:
    if total <= 0:
        total = 1
    filled = int(size * min(current, total) / total)
    return "█" * filled + "░" * (size - filled)
