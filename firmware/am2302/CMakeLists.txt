cmake_minimum_required(VERSION 3.16)
set(PROJECT_VER "0.2.0")
# All components come from the pinned local ESP-IDF; no registry components.
set(ENV{IDF_COMPONENT_MANAGER} "0")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(pcb_mcp_am2302)
