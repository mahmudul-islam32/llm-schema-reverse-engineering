"""Small helpers on top of sqlglot for resolving column references in workloads."""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import exp

Schema = Mapping[str, set[str] | list[str]]


def parse(sql: str, dialect: str = "postgres") -> exp.Expression:
    """Parse one statement; raises ``sqlglot.errors.ParseError`` on failure."""
    expressions = [e for e in sqlglot.parse(sql, read=dialect) if e is not None]
    if len(expressions) != 1:
        raise sqlglot.errors.ParseError(f"expected one statement, got {len(expressions)}")
    expression: exp.Expression = expressions[0]  # type: ignore[assignment]
    # sqlglot is lenient: unknown text can come back as an opaque Command or as a bare
    # expression (``SELEC x`` parses as an alias). Only real statements count as parsed.
    if not isinstance(expression, exp.Query | exp.DML | exp.DDL):
        raise sqlglot.errors.ParseError(f"not a SQL statement: {sql[:60]!r}")
    return expression


def table_aliases(expression: exp.Expression) -> dict[str, str]:
    """Map every alias (and bare table name) in the statement to its table name."""
    aliases: dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        name = table.name.lower()
        if not name:
            continue
        aliases.setdefault(name, name)
        if table.alias:
            aliases[table.alias.lower()] = name
    return aliases


def resolve_column(
    column: exp.Column, aliases: Mapping[str, str], schema: Schema | None
) -> str | None:
    """Return the table a column belongs to, or ``None`` when it is ambiguous."""
    if column.table:
        return aliases.get(column.table.lower())
    if not schema:
        tables = set(aliases.values())
        return next(iter(tables)) if len(tables) == 1 else None
    name = column.name.lower()
    candidates = {
        t for t in set(aliases.values()) if name in {c.lower() for c in schema.get(t, ())}
    }
    return candidates.pop() if len(candidates) == 1 else None


def rename_identifiers(
    sql: str,
    tables: Mapping[str, str],
    columns: Mapping[tuple[str, str], str],
    schema: Schema,
    dialect: str = "postgres",
) -> str:
    """Rewrite a statement so it uses renamed tables/columns.

    ``columns`` maps ``(original_table, original_column)`` to the new column
    name. Raises ``ParseError`` if the statement cannot be parsed.
    """
    expression = parse(sql, dialect)
    aliases = table_aliases(expression)
    planned: list[tuple[exp.Column, str | None, str | None]] = []
    for col in expression.find_all(exp.Column):
        table = resolve_column(col, aliases, schema)
        new_col = columns.get((table, col.name.lower())) if table else None
        new_qual = None
        if col.table and col.table.lower() in tables and col.table.lower() == table:
            new_qual = tables[col.table.lower()]  # qualifier is the bare (unaliased) table name
        planned.append((col, new_col, new_qual))
    for col, new_col, new_qual in planned:
        if new_col:
            col.set("this", exp.to_identifier(new_col))
        if new_qual:
            col.set("table", exp.to_identifier(new_qual))
    for tbl in expression.find_all(exp.Table):
        if tbl.name.lower() in tables:
            tbl.set("this", exp.to_identifier(tables[tbl.name.lower()]))
    return expression.sql(dialect=dialect)
