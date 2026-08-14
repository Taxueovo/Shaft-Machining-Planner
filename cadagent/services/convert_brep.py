"""
================================================

BREP Conversion Service

STEP to BREP format conversion service
CAD file format conversion with PythonOCC

================================================
"""

import os
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.BRepTools import breptools


def convert_stp_to_brep_occ(stp_file, brep_file):
    """
    Convert an STP file to a BREP file with PythonOCC

    Args:
        stp_file: input STEP file path
        brep_file: output BREP file path

    Returns:
        bool: whether the conversion succeeded
    """
    if not os.path.exists(stp_file):
        print(f"Error: input file not found {stp_file}")
        return False

    print(f"Reading STEP file: {stp_file} ...")
    step_reader = STEPControl_Reader()
    status = step_reader.ReadFile(stp_file)

    if status == IFSelect_RetDone:  # ensure the read succeeded
        step_reader.TransferRoots()
        shape = step_reader.OneShape()
        print("STEP file read and converted to a topological shape successfully.")

        # Write the Shape to a BREP file
        print(f"Writing BREP file: {brep_file} ...")
        success = breptools.Write(shape, brep_file)

        if success:
            print(f"Conversion succeeded! Saved to: {brep_file}")
            return True
        else:
            print("Failed to write the BREP file.")
            return False
    else:
        print("Failed to read the STEP file; the file may be corrupted or the format is unsupported.")
        return False


# Test invocation
if __name__ == '__main__':
    # Replace with your actual network drive path or a relative path
    input_stp = r"3D\shaft_1.stp"
    output_brep = r"3D\shaft_1.brep"

    convert_stp_to_brep_occ(input_stp, output_brep)
