"""Guard the spatial-search inputs and execution order of the production models."""
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from core.app.app_core.model_pipeline import MODEL_DESCRIPTIONS

MODELS = Path(__file__).resolve().parents[1] / "core/scripts/qgis/models"
SPATIAL_SEARCH = {
    "native:joinattributesbylocation",
    "native:joinbylocationsummary",
    "native:extractbylocation",
}


class SpatialIndexModelTests(unittest.TestCase):
    def test_spatial_searches_consume_indexed_layers(self):
        for description in MODEL_DESCRIPTIONS:
            with self.subTest(model=description.filename):
                root = ET.parse(MODELS / description.filename).getroot()
                children = root.find('./Option[@name="children"]')
                nodes = {node.get("name"): node for node in children}
                self.assertEqual(len(nodes), len(children), "Duplicate child IDs")
                for name, node in nodes.items():
                    algorithm = node.find('./Option[@name="alg_id"]').get("value")
                    if algorithm not in SPATIAL_SEARCH:
                        continue
                    for parameter in node.findall('./Option[@name="params"]/Option'):
                        if parameter.get("name") not in {"INPUT", "JOIN", "INTERSECT"}:
                            continue
                        source = parameter.find('./Option/Option[@name="child_id"]')
                        self.assertIsNotNone(source, (name, parameter.get("name")))
                        producer = nodes[source.get("value")]
                        self.assertEqual(
                            producer.find('./Option[@name="alg_id"]').get("value"),
                            "native:createspatialindex",
                            (name, parameter.get("name")),
                        )
                # Following the index OUTPUT must enforce ordering without cycles.
                completed, active = set(), set()

                def visit(name):
                    self.assertIn(name, nodes)
                    self.assertNotIn(name, active, "Cyclic model dependency")
                    if name in completed:
                        return
                    active.add(name)
                    for reference in nodes[name].findall(
                        './Option[@name="params"]//Option[@name="child_id"]'
                    ):
                        visit(reference.get("value"))
                    active.remove(name)
                    completed.add(name)

                for name in nodes:
                    visit(name)
