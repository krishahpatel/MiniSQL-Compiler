"""MiniSQL package init."""
from .errors  import ErrorReporter, MiniSQLError
from .lexer   import Lexer, Token, TokenType
from .parser  import Parser, parse
from .catalog import Catalog
from .semantic import SemanticAnalyser
from .ir      import TACProgram
from .irgen   import IRGenerator, generate_ir
from .optimizer import Optimizer
from .codegen import CodeGenerator, generate_bytecode
from .vm      import VM, VMResult
from .storage import Storage

__version__ = "1.0.0"
__all__ = [
    "ErrorReporter", "MiniSQLError",
    "Lexer", "Token", "TokenType",
    "Parser", "parse",
    "Catalog",
    "SemanticAnalyser",
    "TACProgram",
    "IRGenerator", "generate_ir",
    "Optimizer",
    "CodeGenerator", "generate_bytecode",
    "VM", "VMResult",
    "Storage",
]
