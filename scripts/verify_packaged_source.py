#!/usr/bin/env python3
"""Compare a Python source file with a raw marshalled PyInstaller code object."""

from __future__ import annotations

import argparse
import marshal
import types
from pathlib import Path


def canonical(value):
    if isinstance(value, types.CodeType):
        return (
            "code",
            value.co_argcount,
            value.co_posonlyargcount,
            value.co_kwonlyargcount,
            value.co_nlocals,
            value.co_stacksize,
            value.co_flags,
            value.co_code,
            tuple(canonical(item) for item in value.co_consts),
            value.co_names,
            value.co_varnames,
            value.co_freevars,
            value.co_cellvars,
            value.co_firstlineno,
            value.co_linetable,
            value.co_exceptiontable,
        )
    if isinstance(value, tuple):
        return ("tuple", tuple(canonical(item) for item in value))
    if isinstance(value, frozenset):
        items = [canonical(item) for item in value]
        return ("frozenset", tuple(sorted(items, key=repr)))
    return (type(value).__name__, repr(value))


def first_difference(left, right, path="root"):
    if type(left) is not type(right):
        return f"{path}: type {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, types.CodeType):
        fields = (
            "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
            "co_stacksize", "co_flags", "co_code", "co_names", "co_varnames",
            "co_freevars", "co_cellvars", "co_firstlineno", "co_linetable",
            "co_exceptiontable",
        )
        for field in fields:
            if getattr(left, field) != getattr(right, field):
                return f"{path}.{field} differs"
        if len(left.co_consts) != len(right.co_consts):
            return f"{path}.co_consts length differs"
        for index, (left_const, right_const) in enumerate(zip(left.co_consts, right.co_consts)):
            difference = first_difference(left_const, right_const, f"{path}.co_consts[{index}]")
            if difference:
                return difference
        return None
    if isinstance(left, tuple):
        if len(left) != len(right):
            return f"{path}: tuple length differs"
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            difference = first_difference(left_item, right_item, f"{path}[{index}]")
            if difference:
                return difference
        return None
    if isinstance(left, frozenset):
        if canonical(left) != canonical(right):
            return f"{path}: frozenset differs"
        return None
    if left != right:
        return f"{path}: {left!r} != {right!r}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("marshalled", type=Path)
    args = parser.parse_args()

    source_text = args.source.read_text(encoding="utf-8")
    compiled = compile(source_text, str(args.source), "exec", dont_inherit=True)
    with args.marshalled.open("rb") as handle:
        packaged = marshal.load(handle)

    if canonical(compiled) != canonical(packaged):
        print("MISMATCH: source and packaged Python code differ")
        print(first_difference(compiled, packaged))
        return 1
    print("MATCH: source is canonically equivalent to packaged Python code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
