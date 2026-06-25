import ast

source_path = "app/main.py"
with open(source_path, "r", encoding="utf-8") as f:
    tree = ast.parse(f.read())

funcs = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        funcs.append(node.name)

print("Remaining functions in main.py:")
for f in funcs:
    print(f)
print(f"Total: {len(funcs)}")
