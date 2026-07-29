"""AST fact extraction for trace validation.

This layer owns ground-truth extraction from Python source: parsing a file,
locating a function or method, and building a :class:`FunctionInfo`. It performs
no claim comparison, reporting, or process I/O beyond reading the target source
file (the CLI layer owns that boundary decision by passing a path).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .models import FunctionInfo


def get_annotation_str(node: ast.expr | None) -> str | None:
    """Convert AST annotation node to string."""
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def extract_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    """Extract all function/method calls from a function body."""
    calls = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            call_info = {
                "lineno": child.lineno,
                "col_offset": child.col_offset,
            }
            # Get the function being called
            if isinstance(child.func, ast.Name):
                call_info["name"] = child.func.id
                call_info["type"] = "function"
            elif isinstance(child.func, ast.Attribute):
                call_info["name"] = child.func.attr
                call_info["type"] = "method"
                # Try to get the object
                if isinstance(child.func.value, ast.Name):
                    call_info["object"] = child.func.value.id
                else:
                    try:
                        call_info["object"] = ast.unparse(child.func.value)
                    except Exception:
                        call_info["object"] = "<complex>"
            else:
                try:
                    call_info["name"] = ast.unparse(child.func)
                    call_info["type"] = "complex"
                except Exception:
                    call_info["name"] = "<unknown>"
                    call_info["type"] = "unknown"
            calls.append(call_info)
    return calls


def extract_raises(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extract all exception types raised in a function."""
    raises = []
    for child in ast.walk(node):
        if isinstance(child, ast.Raise):
            if child.exc is not None:
                if isinstance(child.exc, ast.Call):
                    if isinstance(child.exc.func, ast.Name):
                        raises.append(child.exc.func.id)
                    elif isinstance(child.exc.func, ast.Attribute):
                        raises.append(child.exc.func.attr)
                elif isinstance(child.exc, ast.Name):
                    raises.append(child.exc.id)
    return list(set(raises))  # Dedupe


def extract_function_info(
    file_path: str,
    func_name: str,
    class_name: str | None = None,
) -> FunctionInfo | None:
    """Extract function information from source file using AST.

    Args:
        file_path: Path to Python source file
        func_name: Name of function to extract
        class_name: If method, the class name (optional)

    Returns:
        FunctionInfo with ground truth, or None if not found
    """
    path = Path(file_path)
    if not path.exists():
        return None

    try:
        source = path.read_text()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return None

    # Top-level function: only a DIRECT child of the module. `ast.walk` yields
    # definitions at every nesting depth, so walking here would return a
    # same-named method or nested function as though it were top-level and
    # validate a claim against the wrong implementation. Restrict to tree.body.
    if class_name is None:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                return _build_function_info(node, file_path)
        return None

    # Method: locate the named class (at any nesting depth) and its direct method.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == func_name:
                    return _build_function_info(
                        item, file_path, is_method=True, class_name=class_name
                    )

    return None


def _build_function_info(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
    is_method: bool = False,
    class_name: str | None = None,
) -> FunctionInfo:
    """Build FunctionInfo from AST node."""
    # Extract arguments
    args = []
    annotations = {}
    defaults = {}

    # Calculate default offset
    num_defaults = len(node.args.defaults)
    num_args = len(node.args.args)
    default_offset = num_args - num_defaults

    for i, arg in enumerate(node.args.args):
        args.append(arg.arg)
        if arg.annotation:
            annotations[arg.arg] = get_annotation_str(arg.annotation) or ""
        # Check for default value
        if i >= default_offset:
            default_idx = i - default_offset
            try:
                defaults[arg.arg] = ast.unparse(node.args.defaults[default_idx])
            except Exception:
                defaults[arg.arg] = "<complex>"

    # Handle *args
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
        if node.args.vararg.annotation:
            annotations[f"*{node.args.vararg.arg}"] = (
                get_annotation_str(node.args.vararg.annotation) or ""
            )

    # Handle **kwargs
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
        if node.args.kwarg.annotation:
            annotations[f"**{node.args.kwarg.arg}"] = (
                get_annotation_str(node.args.kwarg.annotation) or ""
            )

    # Extract decorators
    decorators = []
    for decorator in node.decorator_list:
        try:
            decorators.append(ast.unparse(decorator))
        except Exception:
            decorators.append("<complex>")

    # Extract docstring
    docstring = ast.get_docstring(node)

    return FunctionInfo(
        name=node.name,
        file=file_path,
        lineno=node.lineno,
        end_lineno=getattr(node, "end_lineno", None),
        args=args,
        annotations=annotations,
        returns=get_annotation_str(node.returns),
        defaults=defaults,
        decorators=decorators,
        calls=extract_calls(node),
        raises=extract_raises(node),
        docstring=docstring,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_method=is_method,
        class_name=class_name,
    )
