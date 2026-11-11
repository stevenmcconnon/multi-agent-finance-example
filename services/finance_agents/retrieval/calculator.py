"""Safe arithmetic for the numeric path: numbers, + - * / % **, unary +/-, parentheses. Nothing else.

AST-walked, no eval(), no names, no calls, no attribute access. Exponents are capped and every value is a
float, so "9**9**9" and "((9**1000)**1000)**1000" overflow at once instead of pinning a CPU on a bigint.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable

MAX_EXPONENT = 1000
MAX_LENGTH = 500

_BINARY: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[float], float]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class CalculatorError(ValueError):
    pass


def evaluate(expression: str) -> float:
    if not isinstance(expression, str) or not expression.strip():
        raise CalculatorError("empty expression")
    if len(expression) > MAX_LENGTH:
        raise CalculatorError("expression too long")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"invalid expression: {exc.msg}") from None
    try:
        return _eval(tree.body)
    except OverflowError:
        raise CalculatorError("result too large") from None


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("only numbers are allowed")
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalculatorError(f"exponent above {MAX_EXPONENT}")
        if isinstance(node.op, (ast.Div, ast.Mod)) and right == 0:
            raise CalculatorError("division by zero")
        return _BINARY[type(node.op)](left, right)
    raise CalculatorError(f"disallowed syntax: {type(node).__name__}")
