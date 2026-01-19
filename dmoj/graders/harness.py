import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

from dmoj.cptbox import TracedPopen
from dmoj.executors import executors
from dmoj.executors.base_executor import BaseExecutor
from dmoj.graders.standard import StandardGrader
from dmoj.problem import TestCase
from dmoj.result import Result
from dmoj.utils.unicode import utf8bytes, utf8text


class HarnessGrader(StandardGrader):
    """
    Grader that compiles student submission with hidden harness code.
    Supports Java and Python.

    When skip_precompile is True, the grader bypasses the normal compilation
    step (which would fail if student code references harness classes) and
    handles compilation in the grade() method instead.
    """

    def __init__(self, judge, problem, language, source):
        # Get harness config BEFORE calling super().__init__ (which calls _generate_binary)
        harness_config = problem.config.get('harness_grader', {})
        self.harness = harness_config.get(language, {})
        if not self.harness:
            raise RuntimeError(f'No harness found for language {language}')

        self.skip_precompile = self.harness.get('skip_precompile', False)
        self.harness_code = self.harness.get('code', '')
        self.entry_point = self.harness.get('entry_point', None)

        # Now call parent init (will call _generate_binary)
        super().__init__(judge, problem, language, source)

    def _generate_binary(self) -> Optional[BaseExecutor]:
        """
        Override to skip compilation when skip_precompile is True.
        In this case, compilation is handled in grade() with the harness.
        """
        if self.skip_precompile:
            # Return None - we'll compile in grade() with the harness
            return None
        else:
            # Normal compilation for code that doesn't reference harness
            return super()._generate_binary()

    def grade(self, case: TestCase) -> Result:
        """Override grade to use harness-based execution."""
        result = Result(case)

        handler = self._get_language_handler()
        if not handler:
            result.result_flag = Result.IE
            result.feedback = f'Unsupported harness language: {self.language}'
            return result

        try:
            return handler(case, result)
        except Exception as e:
            result.result_flag = Result.IE
            result.feedback = str(e)
            return result

    def _get_language_handler(self):
        handlers = {
            'JAVA': self._grade_java,
            'JAVA8': self._grade_java,
            'JAVA11': self._grade_java,
            'JAVA17': self._grade_java,
            'JAVA21': self._grade_java,
            'PY3': self._grade_python,
            'PYPY3': self._grade_python,
            'PY2': self._grade_python,
            'PYPY': self._grade_python,
        }
        return handlers.get(self.language)

    def _grade_java(self, case: TestCase, result: Result) -> Result:
        """Java: compile harness + submission together, run harness."""
        work_dir = tempfile.mkdtemp()
        try:
            submission = utf8text(self.source)
            harness_code = self.harness_code
            entry_point = self.entry_point or 'MainTest'

            # Find student's class name(s)
            class_matches = re.findall(r'public\s+class\s+(\w+)', submission)
            student_class = class_matches[0] if class_matches else 'Solution'

            # Write student submission
            student_file = os.path.join(work_dir, f'{student_class}.java')
            with open(student_file, 'w') as f:
                f.write(submission)

            # Write harness code
            harness_file = os.path.join(work_dir, f'{entry_point}.java')
            with open(harness_file, 'w') as f:
                f.write(harness_code)

            # Find Java compiler
            java_home = os.environ.get('JAVA_HOME', '')
            if java_home:
                javac = os.path.join(java_home, 'bin', 'javac')
                java = os.path.join(java_home, 'bin', 'java')
            else:
                javac = 'javac'
                java = 'java'

            # Compile both files together
            compile_result = subprocess.run(
                [javac, student_file, harness_file],
                capture_output=True, cwd=work_dir, timeout=30
            )
            if compile_result.returncode != 0:
                result.result_flag = Result.CE
                result.feedback = self._sanitize_error(
                    compile_result.stderr.decode('utf-8', errors='replace'), work_dir
                )
                return result

            # Get time limit from case config or problem
            time_limit = self.problem.time_limit
            input_data = case.input_data()

            # Run with the harness entry point
            try:
                run_result = subprocess.run(
                    [java, '-cp', work_dir, entry_point],
                    input=input_data, capture_output=True,
                    cwd=work_dir, timeout=time_limit + 1
                )
            except subprocess.TimeoutExpired:
                result.result_flag = Result.TLE
                return result

            # Compare output
            actual = run_result.stdout.replace(b'\r\n', b'\n')
            expected = case.output_data().replace(b'\r\n', b'\n')

            result.proc_output = actual

            if run_result.returncode == 0 and actual.strip() == expected.strip():
                result.result_flag = Result.AC
                result.points = case.points
            else:
                result.result_flag = Result.WA
                if run_result.stderr:
                    result.feedback = self._sanitize_error(
                        run_result.stderr.decode('utf-8', errors='replace'), work_dir
                    )

            return result

        except subprocess.TimeoutExpired:
            result.result_flag = Result.TLE
            return result
        except Exception as e:
            result.result_flag = Result.IE
            result.feedback = str(e)
            return result
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _grade_python(self, case: TestCase, result: Result) -> Result:
        """Python: combine harness + submission, run combined."""
        work_dir = tempfile.mkdtemp()
        try:
            submission = utf8text(self.source)
            harness_code = self.harness_code

            # Replace {SUBMISSION} placeholder with student code
            # Or prepend student code before harness if no placeholder
            if '{SUBMISSION}' in harness_code:
                combined = harness_code.replace('{SUBMISSION}', submission)
            else:
                combined = submission + '\n\n' + harness_code

            script_file = os.path.join(work_dir, 'solution.py')
            with open(script_file, 'w') as f:
                f.write(combined)

            # Get time limit
            time_limit = self.problem.time_limit
            input_data = case.input_data()

            # Determine python command
            if self.language in ('PYPY3', 'PYPY'):
                python_cmd = 'pypy3' if self.language == 'PYPY3' else 'pypy'
            else:
                python_cmd = 'python3' if self.language == 'PY3' else 'python2'

            # Run
            try:
                run_result = subprocess.run(
                    [python_cmd, script_file],
                    input=input_data, capture_output=True,
                    cwd=work_dir, timeout=time_limit + 1
                )
            except subprocess.TimeoutExpired:
                result.result_flag = Result.TLE
                return result

            # Compare output
            actual = run_result.stdout.replace(b'\r\n', b'\n')
            expected = case.output_data().replace(b'\r\n', b'\n')

            result.proc_output = actual

            if run_result.returncode == 0 and actual.strip() == expected.strip():
                result.result_flag = Result.AC
                result.points = case.points
            else:
                result.result_flag = Result.WA
                if run_result.stderr:
                    result.feedback = self._sanitize_error(
                        run_result.stderr.decode('utf-8', errors='replace'), work_dir
                    )

            return result

        except subprocess.TimeoutExpired:
            result.result_flag = Result.TLE
            return result
        except Exception as e:
            result.result_flag = Result.IE
            result.feedback = str(e)
            return result
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _sanitize_error(self, error: str, work_dir: str) -> str:
        """Remove temp paths from error messages."""
        return error.replace(work_dir + os.sep, '').replace(work_dir, '')
