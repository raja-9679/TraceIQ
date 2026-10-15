// Dot-path lookup into a parsed JSON body. Supports `a.b.c`, numeric array
// segments (`items.0.id`), and bracket indexing (`items[0].id`). Returns
// undefined when any segment is missing.
export function jsonPath(obj: any, pathExpr: string): any {
    if (!pathExpr) return undefined;
    let current = obj;
    // normalize [n] → .n so both syntaxes work
    const parts = pathExpr.replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean);
    for (const part of parts) {
        if (current === undefined || current === null) return undefined;
        current = current[part];
    }
    return current;
}
