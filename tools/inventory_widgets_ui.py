"""Reproducible AST call-site inventory for the Widgets → QML migration.

This inventories source-level entry points, not runtime reachability. Imports
and call sites inside closures are included; service object methods whose
receiver cannot be resolved statically remain part of the manual audit.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
DIALOG_TYPES = {"QDialog", "QMessageBox", "QFileDialog", "QInputDialog", "QMenu"}
TASK_NAMES = {"AsyncTaskService", "QThread", "Thread", "ThreadPoolExecutor"}


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    return ""


class Inventory(ast.NodeVisitor):
    def __init__(self):
        self.scope = []
        self.imports = {}
        self.factories = []
        self.dialogs = []
        self.tasks = []
        self.business = []
        self.widget_classes = []

    def location(self, node, name):
        return {"line": node.lineno, "owner": ".".join(self.scope), "call": name}

    def visit_ImportFrom(self, node):
        if node.module:
            for alias in node.names:
                self.imports[alias.asname or alias.name] = node.module + "." + alias.name

    def visit_Import(self, node):
        for alias in node.names:
            self.imports[alias.asname or alias.name.split(".")[0]] = alias.name

    def visit_FunctionDef(self, node):
        if node.name.endswith("_page") or node.name in {"record_editor", "create_main_window", "run_app"}:
            self.factories.append(self.location(node, node.name))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node):
        bases = [dotted(base) for base in node.bases]
        if any(base.startswith("Q") for base in bases):
            self.widget_classes.append({**self.location(node, node.name), "bases": bases})
        if any(base.split(".")[-1] in TASK_NAMES for base in bases):
            self.tasks.append({**self.location(node, node.name), "kind": "worker class", "bases": bases})
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node):
        name = dotted(node.func)
        pieces = name.split(".")
        if any(piece in DIALOG_TYPES for piece in pieces):
            literals = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            self.dialogs.append({**self.location(node, name), "literal_arguments": literals})
        if pieces[-1] in TASK_NAMES or pieces[-1] == "submit":
            self.tasks.append({**self.location(node, name), "kind": "task call"})
        resolved = self.imports.get(pieces[0], pieces[0])
        if len(pieces) > 1:
            resolved += "." + ".".join(pieces[1:])
        if resolved.startswith(("study_app.core.", "study_app.data.", "study_app.ai.", "study_app.integrations.")):
            self.business.append({**self.location(node, name), "service": resolved})
        self.generic_visit(node)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECT / "docs/QML_UI_INVENTORY.json")
    options = parser.parse_args()
    files = []
    for path in sorted((PROJECT / "study_app/ui").glob("*.py")):
        visitor = Inventory()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path)))
        files.append({"file": path.relative_to(PROJECT).as_posix(),
                      "page_factories": visitor.factories, "qt_classes": visitor.widget_classes,
                      "dialogs_and_menus": visitor.dialogs, "worker_and_submit_sites": visitor.tasks,
                      "direct_service_calls": visitor.business})
    totals = {key: sum(len(item[key]) for item in files) for key in (
        "page_factories", "qt_classes", "dialogs_and_menus", "worker_and_submit_sites", "direct_service_calls")}
    payload = {"baseline": "Widgets source unchanged from f8a4d4a", "source_files": len(files),
               "limits": "AST source-level call sites; reachability and service object methods require the manual migration audit.",
               "totals": totals, "files": files}
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_files": len(files), **totals}))


if __name__ == "__main__":
    main()
