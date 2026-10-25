import sys
import pyghidra

PROJECT_LOCATION = "/Users/robmaxwell/projects/f19/analysis/ghidra_project"
PROJECT_NAME = "F19Project"

program_name = sys.argv[1]
script_path = sys.argv[2]

pyghidra.run_script(
    binary_path=None,
    script_path=script_path,
    project_location=PROJECT_LOCATION,
    project_name=PROJECT_NAME,
    program_name=program_name,
    analyze=False,
    nested_project_location=False,
)
