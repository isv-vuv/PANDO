"""Restore assignment selections after their demand segments have been created."""
from xml.etree import ElementTree


def restore_assignment_segments(visum, procedure_xml, log):
    templates = ElementTree.parse(procedure_xml).findall('./OPERATIONS/OPERATION')
    operations = list(visum.Procedures.Operations.GetAll)
    assignments = []
    used = set()
    for template in templates:
        parameters = template.find('PRTASSIGNMENTPARA')
        if parameters is None:
            continue
        segments = parameters.get('DSEGSET', '').strip()
        if segments:
            comment = template.get('COMMENT', '').strip()
            matches = [operation for operation in operations
                       if str(operation.AttValue('Comment') or '').strip() == comment]
            if not comment:
                # OperationType values differ across Visum interfaces. Inspect
                # the actual parameter interface for otherwise unnamed entries.
                assignments_with_parameters = []
                for operation in matches:
                    try:
                        operation.PrTAssignmentParameters.AttValue('DSegSet')
                    except Exception:
                        continue
                    assignments_with_parameters.append(operation)
                matches = assignments_with_parameters
            if len(matches) != 1 or id(matches[0]) in used:
                raise ValueError(
                    f'IV-Umlegung für Nachfragesegmente {segments} nicht eindeutig gefunden '
                    f'(Kommentar: {comment!r}, Treffer: {len(matches)}).')
            used.add(id(matches[0]))
            assignments.append((matches[0], segments))
    for operation, segments in assignments:
        operation.PrTAssignmentParameters.SetAttValue('DSegSet', segments)
        log(f"IV-Umlegung: Nachfragesegmente {segments} aus Vorlage zugewiesen.")
