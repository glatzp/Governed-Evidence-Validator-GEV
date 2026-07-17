import unittest
import subprocess
import sys
import os

class TestBoundary(unittest.TestCase):
    def test_validator_importable(self):
        try:
            from validator.main import run_validation_pipeline
            self.assertTrue(callable(run_validation_pipeline))
        except ImportError:
            self.fail("run_validation_pipeline could not be imported")

    def test_direct_execution_blocked(self):
        env = os.environ.copy()
        # Ensure the python path contains the project root so validator can be found
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env["PYTHONPATH"] = project_root
        
        script_path = os.path.join(project_root, "validator", "main.py")
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            env=env
        )
        
        self.assertNotEqual(result.returncode, 0, "Validator direct execution should exit with non-zero status")
        self.assertIn("This validator engine is sealed and must not be executed directly.", result.stdout)
        self.assertIn("Use: python -m governed.governed_app", result.stdout)

if __name__ == "__main__":
    unittest.main()
