"""Funções utilitárias puras (testáveis sem WhatsApp)."""
import ast
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


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("valor inválido")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
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


def parse_duration(text: str) -> int:
    """Converte '1h30m', '10m', '2d' em segundos. 0 se inválido."""
    if not text:
        return 0
    total = 0
    for amount, unit in _DURATION_RE.findall(text):
        total += int(amount) * _UNIT_SECONDS[unit.lower()]
    if total == 0 and text.strip().isdigit():
        total = int(text.strip()) * 60  # número puro = minutos
    return total


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
