"""Common offline entry point for the existing experimental study CLIs."""
import argparse
import importlib
import sys

COMMAND = 'research-study'
DESCRIPTION = 'Offline experimental assessments; not canonical clinical acceptance'
MODULES = {
    'citation': 'citation_assessment_cli',
    'scribe': 'scribe_assessment_cli',
    'decision': 'decision_assessment_cli',
    'robustness': 'robustness_assessment_cli',
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='clinical-ai-eval '+COMMAND, description=DESCRIPTION,
                                     epilog='Select a study followed by --help for its native workflow.')
    parser.add_argument('study', choices=tuple(MODULES))
    # Native argument parsers own every option after the fixed study name.
    selected = parser.parse_args(argv[:1]).study
    module = importlib.import_module('.'+MODULES[selected], package=__package__)
    return module.main(argv[1:])
