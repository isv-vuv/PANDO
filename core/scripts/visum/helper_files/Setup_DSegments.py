# ==============================================================================
# 1. VERKEHRSSYSTEM & MODUS 'PUTCAR' IDEMPOTENT EINRICHTEN
# ==============================================================================
if 'Visum' not in globals() and 'visum' in globals():
    Visum = visum

code_tsys = "PUTCAR"


def _split_tsys(raw: str) -> list[str]:
    return [t.strip() for t in str(raw or "").split(",") if t.strip()]


try:
    Visum.Net.TSystems.ItemByKey(code_tsys)
    print(f"Transport system '{code_tsys}' already exists.")
except Exception:
    Visum.Net.AddTSystem(code_tsys, "PRT")
    print(f"Transport system '{code_tsys}' (Type: PRT) newly created.")

# 1a) LinkTypes: PUTCAR exakt auf alle Streckentypen setzen, auf denen MINIBUS erlaubt ist
updated_lt = 0
for lt in Visum.Net.LinkTypes.GetAll:
    tsys_list = _split_tsys(lt.AttValue("TSysSet"))
    has_minibus = "MINIBUS" in tsys_list
    has_putcar = code_tsys in tsys_list
    if has_minibus and not has_putcar:
        tsys_list.append(code_tsys)
        lt.SetAttValue("TSysSet", ",".join(tsys_list))
        updated_lt += 1
    elif not has_minibus and has_putcar:
        lt.SetAttValue("TSysSet", ",".join(t for t in tsys_list if t != code_tsys))
        updated_lt += 1
print(f"{updated_lt} link types updated with '{code_tsys}'.")

# 1b) Links: PUTCAR exakt synchron zu MINIBUS auf Strecken setzen
link_updates = []
for vid, raw_tsys in Visum.Net.Links.GetMultiAttValues("TSYSSET", False):
    tsys_list = _split_tsys(raw_tsys)
    has_minibus = "MINIBUS" in tsys_list
    has_putcar = code_tsys in tsys_list
    if has_minibus and not has_putcar:
        link_updates.append((vid, ",".join(tsys_list + [code_tsys])))
    elif not has_minibus and has_putcar:
        link_updates.append((vid, ",".join(t for t in tsys_list if t != code_tsys)))
if link_updates:
    Visum.Net.Links.SetMultiAttValues("TSYSSET", link_updates)
print(f"{len(link_updates)} links updated with '{code_tsys}' (matching MINIBUS).")

# 1c) Turns: PUTCAR für offene CAR-Abbieger zwischen PUTCAR-fähigen Strecken freigeben
turn_updates = []
turn_rows = Visum.Net.Turns.GetMultipleAttributes(
    ["TSYSSET", r"FROMLINK\TSYSSET", r"TOLINK\TSYSSET"], False
)
for idx, (raw_t, raw_from, raw_to) in enumerate(turn_rows, start=1):
    t_list = _split_tsys(raw_t)
    from_ok = code_tsys in _split_tsys(raw_from)
    to_ok = code_tsys in _split_tsys(raw_to)
    car_ok = "CAR" in t_list
    want_putcar = car_ok and from_ok and to_ok
    has_putcar = code_tsys in t_list
    if want_putcar and not has_putcar:
        turn_updates.append((idx, ",".join(t_list + [code_tsys])))
    elif not want_putcar and has_putcar:
        turn_updates.append((idx, ",".join(t for t in t_list if t != code_tsys)))
if turn_updates:
    Visum.Net.Turns.SetMultiAttValues("TSYSSET", turn_updates)
print(f"{len(turn_updates)} turns updated with '{code_tsys}'.")

# 1d) Connectors: Anbindungen für PUTCAR freigeben, wenn CAR offen ist
conn_updates = []
for idx, (_, raw_c) in enumerate(Visum.Net.Connectors.GetMultiAttValues("TSYSSET", False), start=1):
    c_list = _split_tsys(raw_c)
    if "CAR" in c_list and code_tsys not in c_list:
        conn_updates.append((idx, ",".join(c_list + [code_tsys])))
if conn_updates:
    Visum.Net.Connectors.SetMultiAttValues("TSYSSET", conn_updates)
print(f"{len(conn_updates)} connectors updated with '{code_tsys}'.")

try:
    Visum.Net.Modes.ItemByKey(code_tsys)
    print(f"Mode '{code_tsys}' already exists.")
except Exception:
    Visum.Net.AddMode(code_tsys, code_tsys)
    print(f"Mode '{code_tsys}' created.")

# Alten PUTCAR-Nachfragesektor bereinigen (falls vorhanden)
try:
    dseg_obj = Visum.Net.DemandSegments.ItemByKey("PUTCAR")
    Visum.Net.RemoveDemandSegment(dseg_obj)
    print("Old demand segment 'PUTCAR' deleted.")
except Exception:
    pass


# ==============================================================================
# 2. MODALE UND CFL-NACHFRAGESEGMENTE ANLEGEN
# ==============================================================================
existing_dsegs = {ds.AttValue("CODE") for ds in Visum.Net.DemandSegments.GetAll}

# A) Modale Nachfragesegmente (aus Skript 1)
target_modal_dsegs = {
    "W": "WALK",
    "PUTCar": "PUTCAR",
    "C": "CAR",
    "X": "PuT",
    "B": "BIKE"
}

for dseg_code, mode_code in target_modal_dsegs.items():
    if dseg_code not in existing_dsegs:
        try:
            Visum.Net.AddDemandSegment(dseg_code, mode_code)
            existing_dsegs.add(dseg_code)
            print(f"Demand segment '{dseg_code}' (Mode: {mode_code}) created.")
        except Exception as e:
            print(f"Error creating demand segment '{dseg_code}': {e}")

# Ermittle den tatsächlichen Modus-Code für Pkw im Modell (aus Segment 'C' oder Fallback)
car_mode_code = "CAR"
if "C" in existing_dsegs:
    try:
        car_mode_code = Visum.Net.DemandSegments.ItemByKey("C").AttValue("ModeCode")
    except Exception:
        pass

# B) CFL-Nachfragesegmente anlegen und mit existierenden RIN-Matrizen verknüpfen
cfl_dseg_matrices = {
    "CFL0": "RIN_CFL_0_n=2",
    "CFL1": "RIN_CFL_1_n=2",
    "CFL2": "RIN_CFL_2_n=2",
    "CFL3": "RIN_CFL_3_n=5",
    "CFL4": "RIN_CFL_4_n=5"
}

target_ts_no = "0"

for dseg_code, mat_code in cfl_dseg_matrices.items():
    # 1. Segment anlegen falls nicht vorhanden
    if dseg_code not in existing_dsegs:
        try:
            Visum.Net.AddDemandSegment(dseg_code, car_mode_code)
            existing_dsegs.add(dseg_code)
            print(f"CFL demand segment '{dseg_code}' (Mode: {car_mode_code}) created.")
        except Exception as e:
            print(f"Error creating CFL segment '{dseg_code}': {e}")
            continue

    # 2. Verknüpfung mit der bereits existierenden RIN-Matrix setzen
    try:
        dseg_desc = Visum.Net.DemandSegments.ItemByKey(dseg_code).GetDemandDescription()
        dseg_desc.SetAttValue("DemandTimeSeriesNo", target_ts_no)
        dseg_desc.SetAttValue("Matrix", f'Matrix([CODE]="{mat_code}")')
        print(f"Linked '{dseg_code}' -> Matrix '{mat_code}'")
    except Exception as e:
        print(f"Error linking matrix for '{dseg_code}': {e}")


# ==============================================================================
# 3. VERFAHRENSABLAUF: NACHFRAGESEGMENTE DEN OPERATIONS ZUWEISEN
# ==============================================================================
procedure_assignments = {
    # Zuweisungen für CFL-Umlegungen
    "CFL 0/1 assignment": "CFL0,CFL1",
    "CFL 2 assignment": "CFL2",
    "CFL 3 assignment": "CFL3",
    "CFL 4 assignment": "CFL4",
    
    # Zuweisungen für Reisezeiten / Erreichbarkeit
    "Travel time Car": "C",
    "Travel time Bike": "B",
    "Travel time Walk": "W",
    "Travel time PuT (simplified time estimation, fast)": "PUTCar"
}

for procedure in Visum.Procedures.Operations.GetAll:
    comment = procedure.AttValue("Comment") or ""
    op_type = procedure.AttValue("OperationType")
    
    for target_comment, dseg_val in procedure_assignments.items():
        if target_comment in comment:
            try:
                # Typ 101 / CFL-Umlegung (erwartet DSegSet)
                if op_type == 101 or "CFL" in target_comment:
                    procedure.PrTAssignmentParameters.SetAttValue("DSegSet", dseg_val)
                    print(f"[Operation] Set DSegSet '{dseg_val}' -> '{comment}'")
                
                # Typ 103: Kenngrößenmatrix berechnen (erwartet DSeg)
                elif op_type == 103:
                    procedure.PrTSkimMatrixParameters.SetAttValue("DSeg", dseg_val)
                    print(f"[Operation] Set DSeg '{dseg_val}' -> '{comment}'")

            except Exception as e:
                print(f"Error setting DSeg for '{comment}' (Type {op_type}): {e}")
            break

print("\n--- Setup vor Verfahrensstart erfolgreich abgeschlossen! ---")