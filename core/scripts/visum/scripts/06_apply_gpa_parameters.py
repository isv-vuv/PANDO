"""Backwards-compatibility forwarder for apply_gpa_parameters_to_visum."""

import importlib

def apply_gpa_parameters_to_visum(target_project_dir=None, visum=None):
    mod = importlib.import_module("07_apply_gpa_parameters")
    return mod.apply_gpa_parameters_to_visum(target_project_dir=target_project_dir, visum=visum)

def get_model_city_name(base_project_dir):
    mod = importlib.import_module("07_apply_gpa_parameters")
    return mod.get_model_city_name(base_project_dir)

if __name__ == "__main__":
    apply_gpa_parameters_to_visum()
