#!/usr/bin/env python3
"""Static check for Flask routing mistakes in app.py.

Written after a helper function was accidentally inserted between a route
decorator and its handler, which silently rebound the URL to the helper and
broke every install-date change. Cheap to run, catches a whole class of bug:

  * a handler whose arguments don't match the <placeholders> in its path
  * a private helper (leading underscore) exposed as a route
  * two routes registered for the same path and method

Run:  python3 check_routes.py
"""
import ast, re, sys, collections

def main(path='app.py'):
    tree = ast.parse(open(path).read())
    routes, problems = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if getattr(dec.func, 'attr', getattr(dec.func, 'id', '')) != 'route':
                continue
            url = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else '?'
            methods = []
            for kw in dec.keywords:
                if kw.arg == 'methods' and isinstance(kw.value, ast.List):
                    methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
            methods = tuple(sorted(methods)) or ('GET',)
            routes.append((url, methods, node.name))

            placeholders = re.findall(r'<(?:[^:<>]+:)?([^<>]+)>', url)
            argnames = [a.arg for a in node.args.args]
            if placeholders != argnames:
                problems.append(f'{node.name}  {url}\n     takes {argnames} but the path provides {placeholders}')
            if node.name.startswith('_'):
                problems.append(f'{node.name}  {url}\n     private helper exposed as a route — '
                                f'a decorator probably ended up above the wrong function')

    dupes = [k for k, n in collections.Counter((u, m) for u, m, _ in routes).items() if n > 1]
    print(f'{len(routes)} routes checked')
    for p in problems:
        print('  PROBLEM: ' + p)
    for d in dupes:
        print(f'  DUPLICATE: {d[0]} {d[1]}')
    if problems or dupes:
        return 1
    print('OK — no routing problems')
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'app.py'))
