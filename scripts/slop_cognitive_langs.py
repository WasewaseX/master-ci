"""Per-language cognitive-complexity tables, grounded in the grammars.

Every node-type name here was probed against the actual tree-sitter
grammar (scripts/probe_ts_nodes*.py in the working notes), not guessed:
control structures cost 1 + current nesting, branch labels (else, case
arms) cost 1 flat, logical-operator sequences cost 1 per run, and
function-like roots reset the nesting counter the way a reader resets
their context when a closure starts.
"""

from __future__ import annotations

from dataclasses import dataclass

# detector file extension -> tree-sitter language name
TS_LANGUAGE_OF_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".cs": "csharp",
    ".c": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".rs": "rust",
    ".swift": "swift",
    ".dart": "dart",
}


@dataclass(frozen=True)
class Grammar:
    """The cognitive-relevant node vocabulary of one language."""

    nest: frozenset[str]  # +1 + nesting; children nest one level deeper
    flat: frozenset[str]  # +1 flat: branch labels and case arms
    funcs: frozenset[str]  # function-like roots: nesting resets inside
    ops: frozenset[str]  # nodes whose text carries && / || / and / or


_PYN = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "for_in_clause",
        "if_clause",
        "match_case",
    }
)
_JSN = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "switch_statement",
        "catch_clause",
    }
)
_JSF = frozenset(
    {
        "function_declaration",
        "function_expression",
        "generator_function",
        "generator_function_declaration",
        "arrow_function",
        "method_definition",
    }
)
_JSO = frozenset({"binary_expression"})
_BINS = frozenset({"binary_expression"})

GRAMMARS: dict[str, Grammar] = {
    "python": Grammar(
        nest=_PYN,
        flat=frozenset({"else_clause", "conditional_expression"}),
        funcs=frozenset({"function_definition", "lambda"}),
        ops=frozenset({"boolean_operator"}),
    ),
    "javascript": Grammar(
        nest=_JSN,
        flat=frozenset({"switch_case", "switch_default", "ternary_expression"}),
        funcs=_JSF,
        ops=_JSO,
    ),
    "typescript": Grammar(
        nest=_JSN,
        flat=frozenset({"switch_case", "switch_default", "ternary_expression"}),
        funcs=_JSF,
        ops=_JSO,
    ),
    "tsx": Grammar(
        nest=_JSN,
        flat=frozenset({"switch_case", "switch_default", "ternary_expression"}),
        funcs=_JSF,
        ops=_JSO,
    ),
    "go": Grammar(
        nest=frozenset(
            {
                "if_statement",
                "for_statement",
                "expression_switch_statement",
                "type_switch_statement",
                "select_statement",
            }
        ),
        flat=frozenset({"expression_case", "default_case", "type_case", "communication_case"}),
        funcs=frozenset({"function_declaration", "method_declaration", "func_literal"}),
        ops=_BINS,
    ),
    "java": Grammar(
        nest=frozenset(
            {
                "if_statement",
                "for_statement",
                "enhanced_for_statement",
                "while_statement",
                "do_statement",
                "switch_expression",
                "catch_clause",
            }
        ),
        flat=frozenset({"switch_block_statement_group", "switch_rule", "ternary_expression"}),
        funcs=frozenset({"method_declaration", "constructor_declaration", "lambda_expression"}),
        ops=_BINS,
    ),
    "ruby": Grammar(
        nest=frozenset({"if", "elsif", "unless", "for", "while", "until", "case", "rescue"}),
        flat=frozenset({"else", "when", "conditional"}),
        funcs=frozenset({"method", "singleton_method", "block", "lambda"}),
        ops=frozenset({"binary"}),
    ),
    "csharp": Grammar(
        nest=frozenset(
            {
                "if_statement",
                "for_statement",
                "foreach_statement",
                "while_statement",
                "do_statement",
                "switch_statement",
                "catch_clause",
            }
        ),
        flat=frozenset({"switch_section", "conditional_expression"}),
        funcs=frozenset(
            {
                "method_declaration",
                "constructor_declaration",
                "local_function_statement",
                "lambda_expression",
                "anonymous_method_expression",
            }
        ),
        ops=_BINS,
    ),
    "cpp": Grammar(
        nest=frozenset(
            {
                "if_statement",
                "for_statement",
                "for_range_loop",
                "while_statement",
                "do_statement",
                "switch_statement",
                "catch_clause",
            }
        ),
        flat=frozenset({"case_statement", "conditional_expression"}),
        funcs=frozenset({"function_definition", "lambda_expression"}),
        ops=_BINS,
    ),
    "rust": Grammar(
        nest=frozenset(
            {
                "if_expression",
                "for_expression",
                "while_expression",
                "loop_expression",
                "match_expression",
            }
        ),
        flat=frozenset({"match_arm"}),
        funcs=frozenset({"function_item", "closure_expression"}),
        ops=_BINS,
    ),
    "php": Grammar(
        nest=frozenset(
            {
                "if_statement",
                "else_if_clause",
                "for_statement",
                "foreach_statement",
                "while_statement",
                "do_statement",
                "switch_statement",
                "catch_clause",
            }
        ),
        flat=frozenset(
            {"else_clause", "case_statement", "default_statement", "conditional_expression"}
        ),
        funcs=frozenset(
            {"function_definition", "method_declaration", "arrow_function", "anonymous_function"}
        ),
        ops=_BINS,
    ),
    "swift": Grammar(
        nest=frozenset(
            {
                "if_statement",
                "guard_statement",
                "for_statement",
                "while_statement",
                "do_statement",
                "switch_statement",
                "catch_block",
            }
        ),
        flat=frozenset({"switch_entry"}),
        funcs=frozenset({"function_declaration", "lambda_literal"}),
        ops=frozenset({"conjunction_expression", "disjunction_expression"}),
    ),
    "kotlin": Grammar(
        nest=frozenset(
            {
                "if_expression",
                "for_statement",
                "while_statement",
                "do_while_statement",
                "when_expression",
            }
        ),
        flat=frozenset({"when_entry"}),
        funcs=frozenset({"function_declaration", "lambda_literal", "anonymous_function"}),
        ops=frozenset({"conjunction_expression", "disjunction_expression"}),
    ),
    "dart": Grammar(
        nest=frozenset(
            {
                "if_statement",
                "for_statement",
                "while_statement",
                "do_statement",
                "switch_statement",
                "catch_clause",
            }
        ),
        flat=frozenset(
            {"switch_statement_case", "switch_statement_default", "conditional_expression"}
        ),
        funcs=frozenset(
            {
                "function_declaration",
                "method_declaration",
                "function_expression",
                "lambda_expression",
            }
        ),
        ops=frozenset({"logical_and_expression", "logical_or_expression"}),
    ),
    "scala": Grammar(
        nest=frozenset({"if_expression", "for_expression", "while_expression", "match_expression"}),
        flat=frozenset({"case_clause"}),
        funcs=frozenset({"function_definition", "function_expression", "lambda_expression"}),
        ops=frozenset({"infix_expression"}),
    ),
}

# operator token text -> sequence kind (shared by every grammar)
OP_KINDS = {
    "&&": "and",
    "and": "and",
    "logical_and_operator": "and",
    "||": "or",
    "or": "or",
    "logical_or_operator": "or",
    "??": "nullish",
}
NAMED_OP_TOKENS = frozenset({"logical_and_operator", "logical_or_operator", "operator_identifier"})
