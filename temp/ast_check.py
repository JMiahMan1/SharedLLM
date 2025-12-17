import ast
import sys

filename = '/home/jeremiah/Summers Drive/Code/SharedLLM/app/logic/media_ops.py'

class StateFinder(ast.NodeVisitor):
    def __init__(self):
        self.in_handle = False
        self.found = []

    def visit_AsyncFunctionDef(self, node):
        if node.name == 'handle_media_command':
            self.in_handle = True
            # We want to visit the body of handle_media_command
            for child in node.body:
                self.visit(child)
            self.in_handle = False

    def visit_FunctionDef(self, node):
        # Visit synchronous inner functions too
        if self.in_handle:
             for child in node.body:
                self.visit(child)

    def visit_Assign(self, node):
        if self.in_handle:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'state':
                    self.found.append(f"Assignment to 'state' at line {node.lineno}")
        self.generic_visit(node)
        
    def visit_Name(self, node):
        if self.in_handle and isinstance(node.ctx, ast.Load) and node.id == 'state':
             self.found.append(f"Usage of 'state' at line {node.lineno}")

try:
    with open(filename, 'r') as f:
        tree = ast.parse(f.read())
    finder = StateFinder()
    finder.visit(tree)
    for msg in finder.found:
        print(msg)
except Exception as e:
    print(f"Error parsing: {e}")
