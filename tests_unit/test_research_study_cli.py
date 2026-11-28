import contextlib
import io
import unittest
from unittest.mock import patch, Mock
from caeval import research_study_cli as cli


class ResearchStudyCLITests(unittest.TestCase):
    def test_allowlisted_dispatch_preserves_arguments_and_result(self):
        args=['plan','--spec','input with spaces.json','--out','plan.json']
        for study, module in cli.MODULES.items():
            with self.subTest(study=study), patch.object(cli.importlib,'import_module') as load:
                load.return_value.main.return_value=1
                self.assertEqual(cli.main([study,*args]),1)
                load.assert_called_once_with('.'+module,package='caeval')
                load.return_value.main.assert_called_once_with(args)

    def test_unknown_study_never_imports_module(self):
        for args in ([], ['unknown'], ['../os'], ['--spec']):
            with patch.object(cli.importlib,'import_module') as load, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as e: cli.main(args)
                self.assertEqual(e.exception.code,2)
                load.assert_not_called()

    def test_help_lists_all_studies_without_importing_native_modules(self):
        out=io.StringIO()
        with patch.object(cli.importlib,'import_module') as load, contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as e: cli.main(['--help'])
            self.assertEqual(e.exception.code,0)
            load.assert_not_called()
        for study in cli.MODULES: self.assertIn(study,out.getvalue())

    def test_native_help_and_success_passthrough(self):
        with patch.object(cli.importlib,'import_module') as load:
            load.return_value.main.return_value=0
            self.assertEqual(cli.main(['scribe','--help']),0)
            load.return_value.main.assert_called_once_with(['--help'])

    def test_no_implicit_network_or_file_arguments(self):
        with patch.object(cli.importlib,'import_module') as load:
            load.return_value.main.side_effect=SystemExit(2)
            with self.assertRaises(SystemExit) as e: cli.main(['decision'])
            self.assertEqual(e.exception.code,2)
            load.return_value.main.assert_called_once_with([])


if __name__=='__main__':unittest.main()
