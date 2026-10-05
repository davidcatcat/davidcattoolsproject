"""Thin product startup boundary; interpreter/site initialization precedes it."""


def main(argv=None):
    try:
        from .cli import main as cli_main
        return cli_main(argv)
    except KeyboardInterrupt:
        import sys
        print('llmtool: interrupted', file=sys.stderr)
        return 130
