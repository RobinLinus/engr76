"""Select original Python declarations from nbformat 4 JSON, not whole cells.

This deliberately narrow importer is not a sandbox. See README.md for the
supported dependency boundary and the optional visualization limitation.
"""

import ast
import builtins
import json
import symtable
from types import ModuleType

REQUIRED_FUNCTIONS = (
    "generate_modulated_signal", "add_silence", "detect_start", "demodulate",
)


class NotebookImportError(ValueError):
    """The notebook cannot supply the required audio declarations."""


def _names(expression):
    return {node.id for node in ast.walk(expression)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


def _literal_expression(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, int, float, bool, type(None)))
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_literal_expression(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and _literal_expression(key) and _literal_expression(value)
                   for key, value in zip(node.keys, node.values))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert)):
        return _literal_expression(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)):
        return _literal_expression(node.left) and _literal_expression(node.right)
    return False


def _annotation(node):
    if isinstance(node, (ast.Name, ast.Constant)):
        return True
    if isinstance(node, ast.Attribute):
        return _annotation(node.value)
    if isinstance(node, ast.Subscript):
        return _annotation(node.value) and _annotation(node.slice)
    if isinstance(node, ast.Tuple):
        return all(_annotation(item) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation(node.left) and _annotation(node.right)
    return False


def _global_references(table):
    names = {symbol.get_name() for symbol in table.get_symbols()
             if symbol.is_referenced() and symbol.is_global()}
    for child in table.get_children():
        names.update(_global_references(child))
    return names


def _visualization_only_references(function):
    """Dependencies used only by the starter's explicit optional plotting body.

    The unimplemented starter also references an undefined start_threshold there.
    Ignore those optional references for selection only; never rewrite the body.
    """
    guarded = set()
    for node in function.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            test = test.values[0]
        if isinstance(test, ast.Name) and test.id == "visual_test":
            for statement in node.body:
                guarded.update(id(child) for child in ast.walk(statement))
    optional, ordinary = set(), set()
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            (optional if id(node) in guarded else ordinary).add(node.id)
    return optional - ordinary


def _bound_names(statement):
    """Record unsupported module bindings without descending into local scopes."""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {statement.name}
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return {alias.asname or alias.name.split(".")[0] for alias in statement.names}
    if isinstance(statement, ast.Name) and isinstance(statement.ctx, (ast.Store, ast.Del)):
        return {statement.id}
    if isinstance(statement, (ast.Attribute, ast.Subscript)) and isinstance(statement.ctx, (ast.Store, ast.Del)):
        root = statement.value
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        return {root.id} if isinstance(root, ast.Name) else set()
    names = set()
    for child in ast.iter_child_nodes(statement):
        names.update(_bound_names(child))
    return names


def import_notebook(content):
    """Accept notebook JSON text/bytes or a decoded dict; return a fresh module.

    __selected_source__ and __declarations__ describe what actually executed.
    No cell outputs, markdown, metadata, filenames or execution counts are used.
    """
    try:
        notebook = json.loads(content) if isinstance(content, (str, bytes)) else content
    except (ValueError, UnicodeError) as error:
        raise NotebookImportError("Notebook content is not valid JSON.") from error
    if not isinstance(notebook, dict) or notebook.get("nbformat") != 4 or not isinstance(notebook.get("cells"), list):
        raise NotebookImportError("Expected an nbformat 4 notebook with a cells list.")
    bindings = {}

    def record(name, node, source, cell):
        bindings.setdefault(name, []).append((node, source, cell))

    for cell_number, cell in enumerate(notebook["cells"], 1):
        if not isinstance(cell, dict):
            raise NotebookImportError(f"Cell {cell_number} must be a JSON object.")
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list) and all(isinstance(line, str) for line in source):
            source = "".join(source)
        if not isinstance(source, str):
            raise NotebookImportError(f"Code cell {cell_number} has invalid source.")
        lines = [line.strip() for line in source.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        # Standalone notebook magics/installers aren't Python declarations.
        if lines and (lines[0].startswith("%%") or all(line.startswith(("%", "!")) for line in lines)):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as error:
            raise NotebookImportError(f"Code cell {cell_number}: unsupported Python syntax or mixed magic (line {error.lineno}).") from error
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and (node.level or any(alias.name == "*" for alias in node.names)):
                    raise NotebookImportError(f"Code cell {cell_number}: relative/star imports are unsupported.")
                for alias in node.names:
                    name = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                    prefix = "import " if isinstance(node, ast.Import) else f"from {node.module} import "
                    selected = prefix + ast.get_source_segment(source, alias)
                    record(name, ast.parse(selected).body[0], selected, cell_number)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                record(node.name, node, ast.get_source_segment(source, node), cell_number)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                record(node.targets[0].id, node, ast.get_source_segment(source, node), cell_number)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                record(node.target.id, node, ast.get_source_segment(source, node), cell_number)
            else:
                for name in _bound_names(node):
                    record(name, node, "", cell_number)

    selected = {}
    eager = {}

    def select(name, owner):
        if name in selected:
            return
        choices = bindings.get(name, [])
        if not choices:
            if name in vars(builtins) or name == "__name__":
                return
            raise NotebookImportError(f"Missing notebook dependency '{name}' required by '{owner}'.")
        node, source, cell = choices[0]
        # Identical literal constants (including 44_100 vs 44100) and imports
        # are harmless; function redefinitions and other conflicts are ambiguous.
        repeatable = isinstance(node, (ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom))
        if len(choices) > 1 and (not repeatable or any(ast.dump(other) != ast.dump(node) for other, _, _ in choices[1:])):
            raise NotebookImportError(f"Ambiguous notebook binding '{name}' in cells {', '.join(str(item[2]) for item in choices)}.")
        required = set()
        immediate = set()
        reason = None
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            pass
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if node.value is None or not _literal_expression(node.value):
                reason = "dynamic top-level initialization; use literal constants/simple arithmetic"
            else:
                immediate = _names(node.value)
            if isinstance(node, ast.AnnAssign):
                if not _annotation(node.annotation):
                    reason = "dynamic annotation"
                immediate |= _names(node.annotation)
        elif isinstance(node, ast.FunctionDef):
            if node.decorator_list or getattr(node, "type_params", []):
                reason = "decorators/type parameters"
            defaults = node.args.defaults + [value for value in node.args.kw_defaults if value is not None]
            annotations = [arg.annotation for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                           *([node.args.vararg] if node.args.vararg else []), *([node.args.kwarg] if node.args.kwarg else []))
                           if arg.annotation is not None]
            if node.returns is not None:
                annotations.append(node.returns)
            if not all(_literal_expression(value) for value in defaults) or not all(_annotation(value) for value in annotations):
                reason = "dynamic defaults/annotations"
            for expression in defaults + annotations:
                immediate |= _names(expression)
            required = _global_references(symtable.symtable(source, "<notebook>", "exec"))
            if name == "detect_start":
                required -= _visualization_only_references(node)
        elif isinstance(node, ast.ClassDef):
            # The public demo needs only a docstring-only ValueError subclass.
            if (node.decorator_list or node.keywords or getattr(node, "type_params", []) or len(node.bases) != 1
                    or not isinstance(node.bases[0], ast.Name)
                    or not isinstance(vars(builtins).get(node.bases[0].id), type)
                    or not issubclass(vars(builtins)[node.bases[0].id], BaseException)
                    or not all(isinstance(item, ast.Pass) or (isinstance(item, ast.Expr)
                        and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str)) for item in node.body)):
                reason = "only simple exception classes are supported"
            else:
                immediate = {node.bases[0].id}
        else:
            reason = "unsupported top-level declaration/assignment"
        if reason:
            raise NotebookImportError(f"Notebook dependency '{name}' (cell {cell}): {reason}.")
        selected[name] = (node, source, cell)
        eager[name] = immediate
        for dependency in sorted(required | immediate):
            select(dependency, name)
        # Arithmetic/default constants cannot depend on imported objects or
        # functions: that would allow dynamic initialization through operators.
        literal_values = [node.value] if isinstance(node, (ast.Assign, ast.AnnAssign)) else defaults if isinstance(node, ast.FunctionDef) else []
        for value in literal_values:
            for dependency in _names(value):
                declaration = selected.get(dependency)
                if declaration is None or not isinstance(declaration[0], (ast.Assign, ast.AnnAssign)):
                    raise NotebookImportError(f"Notebook dependency '{name}': '{dependency}' is not a literal constant.")

    for name in REQUIRED_FUNCTIONS:
        if name not in bindings:
            raise NotebookImportError(f"Missing required notebook function '{name}'.")
        select(name, "audio host")
        if not isinstance(selected[name][0], ast.FunctionDef):
            raise NotebookImportError(f"Required notebook function '{name}' must be a function declaration.")

    ordered, visiting, visited = [], set(), set()

    def order(name):
        if name not in selected or name in visited:
            return
        if name in visiting:
            raise NotebookImportError(f"Cyclic declaration-time dependency at '{name}'.")
        visiting.add(name)
        for dependency in sorted(eager[name]):
            order(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(name)

    for name in selected:
        order(name)
    module = ModuleType("project2a_notebook")
    # Complete validation precedes execution: no partially validated dependency
    # graph or unrelated cell statement is run.
    for name in ordered:
        _, source, cell = selected[name]
        try:
            exec(compile(source, f"<notebook cell {cell}: {name}>", "exec"), module.__dict__)
        except Exception as error:
            raise NotebookImportError(f"Could not load notebook dependency '{name}' (cell {cell}): {error}") from error
    module.__selected_source__ = "\n\n".join(selected[name][1] for name in ordered)
    module.__declarations__ = tuple(ordered)
    return module
