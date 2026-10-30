"""srev: LLM-assisted schema reverse engineering.

Discovers foreign keys (FK), unique column combinations (UCC) and functional
dependencies (FD) in databases whose constraints were never declared, and
benchmarks LLM proposals against statistical profiling baselines.
"""

__version__ = "0.1.0"
