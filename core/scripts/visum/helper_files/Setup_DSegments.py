# ==============================================================================
# 1. VERKEHRSSYSTEM & MODUS 'PUTCAR' EINRICHTEN (aus Skript 1)
# ==============================================================================
if 'Visum' not in globals() and 'visum' in globals():
    Visum = visum

code_tsys = "PUTCAR"

try:
    Visum.Net.TSystems.ItemByKey(code_tsys)
    print(f"Transport system '{code_tsys}' already exists.")
except Exception:
    Visum.Net.AddTSystem(code_tsys, "PRT")
    print(f"Transport system '{code_tsys}' (Type: PRT) newly created.")

    updated_lt = 0
    for lt in Visum.Net.LinkTypes.GetAll:
        tsys_set = lt.AttValue("TSysSet") or ""
        tsys_list = [t.strip() for t in tsys_set.split(",") if t.strip()]
        
        if "MINIBUS" in tsys_list and code_tsys not in tsys_list:
            tsys_list.append(code_tsys)
            lt.SetAttValue("TSysSet", ",".join(tsys_list))
            updated_lt += 1
            
    print(f"{updated_lt} link types updated with '{code_tsys}'.")

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