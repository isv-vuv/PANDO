import os
import shutil
import tempfile
import unittest

from core.app.app_core.processing import ensure_qgis_scripts_installed
from core.app.app_core.project import tool_root


class QgisScriptInstallationTests(unittest.TestCase):
    def test_ensure_qgis_scripts_installed_copies_scripts_and_models(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup mock APPDATA directory structure
            mock_appdata = os.path.join(tmpdir, "AppData", "Roaming")
            mock_qgis4_profile = os.path.join(mock_appdata, "QGIS", "QGIS4", "profiles", "default")
            os.makedirs(mock_qgis4_profile, exist_ok=True)

            orig_appdata = os.environ.get("APPDATA")
            try:
                os.environ["APPDATA"] = mock_appdata
                copied = ensure_qgis_scripts_installed(root_dir=tool_root())
            finally:
                if orig_appdata is not None:
                    os.environ["APPDATA"] = orig_appdata
                else:
                    os.environ.pop("APPDATA", None)

            # Check that scripts were copied
            scripts_dir = os.path.join(mock_qgis4_profile, "processing", "scripts")
            self.assertTrue(os.path.isdir(scripts_dir))
            installed_scripts = os.listdir(scripts_dir)
            self.assertIn("Model2_CityCategorisation.py", installed_scripts)
            self.assertIn("Model4_Export_poly.py", installed_scripts)
            self.assertIn("Model5_RasterNeighborhood.py", installed_scripts)

            # Check that models were copied
            models_dir = os.path.join(mock_qgis4_profile, "processing", "models")
            self.assertTrue(os.path.isdir(models_dir))
            installed_models = os.listdir(models_dir)
            self.assertIn("Model1_DataPrep.model3", installed_models)
            self.assertIn("Model2_ZoneClass.model3", installed_models)
            self.assertIn("Model6_ZoneAssembler.model3", installed_models)


    def test_all_qgis_scripts_and_models_have_pando_group(self):
        root = tool_root()
        scripts_dir = os.path.join(root, "core", "scripts", "qgis", "scripts")
        models_dir = os.path.join(root, "core", "scripts", "qgis", "models")

        # Verify scripts
        for fname in os.listdir(scripts_dir):
            if fname.endswith(".py") and not fname.startswith("__"):
                path = os.path.join(scripts_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("return 'PANDO'", content.replace('"', "'"), f"Script {fname} missing group PANDO")
                self.assertIn("return 'pando'", content.replace('"', "'"), f"Script {fname} missing groupId pando")

        # Verify models
        for fname in os.listdir(models_dir):
            if fname.endswith(".model3") and not any(tag in fname.lower() for tag in ("_v0", "_backup", "_old")):
                path = os.path.join(models_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                stem = os.path.splitext(fname)[0]
                self.assertTrue(
                    'name="model_group" type="QString" value="PANDO"' in content
                    or 'value="PANDO" type="QString" name="model_group"' in content
                    or 'type="QString" name="model_group" value="PANDO"' in content,
                    f"Model {fname} missing model_group PANDO",
                )
                self.assertTrue(
                    f'name="model_name" type="QString" value="{stem}"' in content
                    or f'value="{stem}" type="QString" name="model_name"' in content
                    or f'type="QString" name="model_name" value="{stem}"' in content,
                    f"Model {fname} missing model_name {stem}",
                )


if __name__ == "__main__":
    unittest.main()
