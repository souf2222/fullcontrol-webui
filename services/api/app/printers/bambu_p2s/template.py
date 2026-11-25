"""Bambu template grammar: parser, resolver, and inventory.

Bambu Studio machine templates (machine_start_gcode etc.) use a
PrusaSlicer-style placeholder language with Orca/Bambu extensions:

- ``{expression}``        evaluated expressions (arithmetic, comparisons,
                          subscripting ``name[index]``, functions max/min/
                          ceil/floor/round/int/abs, string comparisons)
- ``{if ...}``/``{elsif ...}``/``{else}``/``{endif}``   conditional blocks
                          (note: ``{elsif}`` is an Orca extension, multi-line
                          expressions are allowed, ``&&``/``||`` operators)
- ``[name]`` and ``[name[0]]``  plain text substitution

Everything is evaluated with a whitelisted AST walker — no eval() of
arbitrary content. Unknown variables are hard errors (fail loud), never
silently substituted.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field


class TemplateError(Exception):
    """Raised when a template cannot be resolved. `errors` lists reasons."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class _Tag:
    kind: str          # 'text' | 'expr' | 'if' | 'elsif' | 'else' | 'endif'
    content: str = ""
    children: list = field(default_factory=list)


def _tokenize(text: str) -> list[_Tag]:
    """Split template text into text / expression / structural tags.

    Expressions and tags never contain nested braces, so scanning to the
    next '}' is safe for a single tag; block nesting is reconstructed later.
    """
    tokens: list[_Tag] = []
    pos = 0
    while pos < len(text):
        brace = text.find("{", pos)
        if brace == -1:
            tokens.append(_Tag("text", text[pos:]))
            break
        if brace > pos:
            tokens.append(_Tag("text", text[pos:brace]))
        close = text.find("}", brace)
        if close == -1:
            raise TemplateError([f"unterminated '{{' at offset {brace}"])
        inner = text[brace + 1: close]
        head = inner.strip()
        if head.startswith("if "):
            tokens.append(_Tag("if", head[3:]))
        elif head.startswith("elsif "):
            tokens.append(_Tag("elsif", head[6:]))
        elif head == "else":
            tokens.append(_Tag("else"))
        elif head == "endif":
            tokens.append(_Tag("endif"))
        else:
            tokens.append(_Tag("expr", inner))
        pos = close + 1
    return tokens


def _parse_tree(tokens: list[_Tag]) -> list[_Tag]:
    """Reconstruct nested {if}...{endif} blocks into a tree.

    An {if}/{elsif}/{else} chain is represented as sibling tags at the same
    level, each with its arm body in `children`; {endif} closes the chain.
    """
    root: list[_Tag] = []
    stack: list[list[_Tag]] = [root]
    open_blocks = 0
    for tok in tokens:
        if tok.kind == "if":
            stack[-1].append(tok)
            stack.append(tok.children)
            open_blocks += 1
        elif tok.kind in ("elsif", "else"):
            stack.pop()                 # leave the previous arm
            stack[-1].append(tok)       # sibling arm in the enclosing frame
            stack.append(tok.children)
        elif tok.kind == "endif":
            stack.pop()
            open_blocks -= 1
        else:
            stack[-1].append(tok)
    if open_blocks != 0 or len(stack) != 1:
        raise TemplateError([f"unterminated '{{if}}' block(s) ({open_blocks})"])
    return root


def _normalize(expr: str) -> str:
    return expr.replace("&&", " and ").replace("||", " or ").replace("\n", " ")


_ALLOWED_FUNCS = {
    "max": max, "min": min, "ceil": math.ceil, "floor": math.floor,
    "round": round, "int": int, "float": float, "abs": abs,
}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp,
    ast.Call, ast.Name, ast.Load, ast.Subscript,
    ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
    ast.USub, ast.UAdd, ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq,
    ast.NotEq, ast.And, ast.Or, ast.Not, ast.Tuple, ast.keyword,
    ast.IfExp, ast.Mod,
)


def _coerce(value, other=None):
    """Numeric coercion matching Bambu's loose typing ('0.4' == 0.4)."""
    if isinstance(value, str):
        s = value.strip()
        if other is not None and isinstance(other, (int, float)) and not isinstance(other, bool):
            try:
                return float(s)
            except ValueError:
                return value
        if other is not None and isinstance(other, str):
            return value
        try:
            return float(s)
        except ValueError:
            return value
    return value


class _Evaluator(ast.NodeVisitor):
    def __init__(self, variables: dict[str, object], current_extruder: int):
        self.vars = variables
        self.current_extruder = current_extruder

    # -- node dispatch -----------------------------------------------------
    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        return node.value

    def visit_Name(self, node):
        if node.id not in self.vars:
            raise TemplateError([f"unknown variable '{node.id}'"])
        return self.vars[node.id]

    def visit_Subscript(self, node):
        base = self.visit(node.value)
        idx = self.visit(node.slice)
        if isinstance(base, str):
            base = _coerce(base)
        if not isinstance(base, (list, tuple)):
            raise TemplateError([f"variable is not a list: index requested"])
        i = int(idx)
        if i < 0 or i >= len(base):
            raise TemplateError([f"index {i} out of range for list variable"])
        return base[i]

    def visit_BinOp(self, node):
        left = _coerce(self.visit(node.left))
        right = _coerce(self.visit(node.right), left)
        return {
            ast.Add: lambda: left + right,
            ast.Sub: lambda: left - right,
            ast.Mult: lambda: left * right,
            ast.Div: lambda: left / right,
            ast.FloorDiv: lambda: left // right,
            ast.Mod: lambda: left % right,
        }[type(node.op)]()

    def visit_UnaryOp(self, node):
        v = _coerce(self.visit(node.operand))
        return -v if isinstance(node.op, ast.USub) else +v

    def visit_Compare(self, node):
        left = self.visit(node.left)
        result = True
        for op, comparator in zip(node.ops, node.comparators):
            l = _coerce(left, self.peek(comparator))
            r = _coerce(self.visit(comparator), left)
            if isinstance(l, str) != isinstance(r, str):
                # string vs number after failed coercion: treat as unequal
                result = isinstance(op, ast.NotEq)
                left = r
                continue
            result = result and _cmp(op, l, r)
            left = r
        return result

    @staticmethod
    def peek(node) -> object:
        """Best-effort look at a comparator's literal type for coercion."""
        try:
            return ast.literal_eval(node)
        except Exception:
            return None

    def visit_BoolOp(self, node):
        vals = [self.visit(v) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(_truthy(v) for v in vals)
        return any(_truthy(v) for v in vals)

    def visit_Not(self, node):  # pragma: no cover - via UnaryOp
        return not _truthy(self.visit(node.operand))

    def visit_IfExp(self, node):
        return self.visit(node.body) if _truthy(self.visit(node.test)) else self.visit(node.orelse)

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise TemplateError([f"function not allowed: {ast.dump(node.func)}"])
        args = [self.visit(a) for a in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)

    def generic_visit(self, node):
        if not isinstance(node, _ALLOWED_NODES):
            raise TemplateError([f"expression construct not allowed: {type(node).__name__}"])
        return super().generic_visit(node)


def _cmp(op, l, r):
    return {ast.Gt: l > r, ast.Lt: l < r, ast.GtE: l >= r,
            ast.LtE: l <= r, ast.Eq: l == r, ast.NotEq: l != r}[type(op)]


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() not in ("", "false", "0", "nil", "none")
    return bool(v)


def _format(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


_SQUARE_VAR = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?\]")
_FORMAT_SPEC = re.compile(r"^(.*?):(\.\d+)[fd]$")  # Orca-style '{expr:.3f}' suffix


class TemplateResolver:
    """Resolves Bambu templates against a settings mapping.

    `variables` must contain every referenced setting; missing ones raise
    TemplateError listing all offenders. List-valued settings used as plain
    `[name]` resolve to the `current_extruder` element.
    """

    def __init__(self, variables: dict[str, object], current_extruder: int = 0):
        self.vars = variables
        self.current_extruder = current_extruder

    def _eval_expr(self, expr: str):
        spec: str | None = None
        m = _FORMAT_SPEC.match(expr.strip())
        if m:
            expr, spec = m.group(1), m.group(2) + "f"
        tree = ast.parse(_normalize(expr), mode="eval")
        value = _Evaluator(self.vars, self.current_extruder).visit(tree)
        if spec is not None:
            return format(float(value), spec)
        return value

    # -- inventory ----------------------------------------------------------
    def inventory(self, tokens_or_text) -> dict:
        text = tokens_or_text if isinstance(tokens_or_text, str) else ""
        tokens = _tokenize(text) if isinstance(tokens_or_text, str) else tokens_or_text
        names: set[str] = set()

        def walk(nodes):
            for t in nodes:
                if t.kind in ("text",):
                    for m in _SQUARE_VAR.finditer(t.content):
                        names.add(m.group(1))
                elif t.kind in ("expr", "if", "elsif"):
                    expr = _normalize(t.content)
                    try:
                        tree = ast.parse(expr, mode="eval")
                    except SyntaxError:
                        continue
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Name):
                            names.add(node.id)
                        if isinstance(node, ast.Constant) and isinstance(node.value, str):
                            pass  # string literals are not variables
                elif t.kind == "else":
                    walk(t.children)
                if t.kind in ("if", "elsif", "else"):
                    walk(t.children)

        walk(tokens)
        return {"variables": sorted(names)}

    # -- resolution ----------------------------------------------------------
    def resolve(self, template: str) -> str:
        errors: list[str] = []
        result = self._resolve_nodes(_parse_tree(_tokenize(template)), errors)
        if errors:
            raise TemplateError(sorted(set(errors)))
        return result

    def _resolve_nodes(self, nodes: list[_Tag], errors: list[str]) -> str:
        out: list[str] = []
        i = 0
        while i < len(nodes):
            tok = nodes[i]
            if tok.kind == "text":
                out.append(self._substitute_square(tok.content, errors))
            elif tok.kind == "expr":
                try:
                    out.append(_format(self._eval_expr(tok.content)))
                except TemplateError as exc:
                    errors.extend(exc.errors)
            elif tok.kind == "if":
                arm_values: list[tuple[str | None, list[_Tag]]] = []
                cond = tok
                arm_values.append((cond.content, cond.children))
                j = i + 1
                while j < len(nodes) and nodes[j].kind in ("elsif", "else"):
                    arm_values.append((nodes[j].content if nodes[j].kind == "elsif" else None, nodes[j].children))
                    j += 1
                taken = False
                for cond_text, children in arm_values:
                    if cond_text is None:          # else arm
                        out.append(self._resolve_nodes(children, errors))
                        taken = True
                        break
                    try:
                        if _truthy(self._eval_expr(cond_text)):
                            out.append(self._resolve_nodes(children, errors))
                            taken = True
                            break
                    except TemplateError as exc:
                        errors.extend(exc.errors)
                        taken = True
                        break
                if not taken:
                    pass  # no arm taken: emit nothing
                i = j
                continue
            i += 1
        return "".join(out)

    def _substitute_square(self, text: str, errors: list[str]) -> str:
        def repl(m: re.Match) -> str:
            name, idx = m.group(1), m.group(2)
            if name not in self.vars:
                errors.append(f"unknown variable '{name}'")
                return m.group(0)
            value = self.vars[name]
            if isinstance(value, (list, tuple)):
                i = int(idx) if idx is not None else self.current_extruder
                if i >= len(value):
                    errors.append(f"index {i} out of range for '{name}'")
                    return m.group(0)
                value = value[i]
            return _format(value)

        return _SQUARE_VAR.sub(repl, text)


def inventory(template: str) -> dict:
    """Variable inventory for a template (public helper)."""
    tokens = _tokenize(template)
    resolver = TemplateResolver({})
    return resolver.inventory(tokens)
