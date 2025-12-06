import os
import re
import ast
import sys
import glob

# Constants
FORBIDDEN_PATTERNS = [
    r"# \.\.\.",  # Placeholder comments
    r"pass  # TODO",
]

CRITICAL_FILES = [
    "app/main.py",
    "app/logic.py",
    "app/settings.py",
    "app/intent_engine.py",
]

def check_forbidden_patterns(file_path):
    """Checks for forbidden specific patterns in a file."""
    issues = []
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.readlines()
        
    for i, line in enumerate(content):
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, line):
                issues.append(f"Line {i+1}: Found forbidden pattern '{pattern}'")
    return issues

def check_circular_imports():
    """Simple static analysis to detect obvious circular imports in app/."""
    # This is a heuristic check.
    imports = {}
    
    # 1. Parse all python files in app/
    files = glob.glob("app/**/*.py", recursive=True)
    
    for fpath in files:
        module_name = fpath.replace("/", ".").replace(".py", "")
        if module_name.endswith(".__init__"):
            module_name = module_name[:-9]
            
        with open(fpath, "r") as f:
            try:
                tree = ast.parse(f.read())
            except SyntaxError :
                continue

            node_imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        node_imports.add(n.name.split('.')[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        node_imports.add(node.module.split('.')[0])
            
            imports[module_name] = node_imports

    # 2. Check for A -> B -> A
    circular = []
    for mod, deps in imports.items():
        if "app" in mod: # Only care about internal circular deps mostly
            for dep in deps:
                # Naive check: if 'app.settings' imports 'app.logic' and 'app.logic' imports 'app.settings'
                # Map dep back to potential file
                pass 
                # Implementing a full graph cycle check is complex, 
                # here we just check for direct imports between settings and logic/main as requested
                
    # Specific Rule: settings.py MUST NOT import logic or main
    issues = []
    settings_path = "app/settings.py"
    if os.path.exists(settings_path):
        with open(settings_path, "r") as f:
            content = f.read()
            if "from logic" in content or "import logic" in content:
                issues.append("app/settings.py imports 'logic' (Circular Risk!)")
            if "from main" in content or "import main" in content:
                issues.append("app/settings.py imports 'main' (Circular Risk!)")

    return issues

def inventory_functions(file_path):
    """Returns a set of function names defined in the file."""
    with open(file_path, "r") as f:
        tree = ast.parse(f.read())
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

def compare_inventory(current_inventory, baseline_inventory):
    """Checks if any functions were deleted."""
    missing = baseline_inventory - current_inventory
    return list(missing)

def main():
    print("--- 🛡️  VERIFYING INTEGRITY 🛡️ ---")
    all_passed = True
    
    # 1. Forbidden Patterns
    print("\n[1] Checking for forbidden patterns (incomplete code)...")
    files_to_check = glob.glob("app/**/*.py", recursive=True)
    for f in files_to_check:
        issues = check_forbidden_patterns(f)
        if issues:
            print(f"❌ {f} FAILED:")
            for issue in issues:
                print(f"   - {issue}")
            all_passed = False
    
    # 2. Import Audit
    print("\n[2] Checking for dangerous circular imports...")
    import_issues = check_circular_imports()
    if import_issues:
        print("❌ CIRCULAR IMPORT RISKS:")
        for issue in import_issues:
            print(f"   - {issue}")
        all_passed = False
    else:
        print("✅ Settings.py is clean.")

    # 3. Simple Syntax Check
    print("\n[3] Verifying syntax...")
    for f in files_to_check:
        try:
            with open(f, "r") as f_obj:
                ast.parse(f_obj.read())
        except SyntaxError as e:
            print(f"❌ SYNTAX ERROR in {f}: {e}")
            all_passed = False

    if all_passed:
        print("\n✅ INTEGRITY CHECK PASSED.")
        sys.exit(0)
    else:
        print("\n❌ INTEGRITY CHECK FAILED.")
        sys.exit(1)

if __name__ == "__main__":
    main()
