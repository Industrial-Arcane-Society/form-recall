cmake_minimum_required(VERSION 3.24)
project(occt_bridge VERSION 0.1.0 LANGUAGES CXX)

if(NOT DEFINED OCCT_ROOT)
  message(FATAL_ERROR "Set OCCT_ROOT to the project-local OCCT install directory")
endif()

find_path(OCCT_INCLUDE_DIR Standard_Version.hxx
  PATHS "${OCCT_ROOT}/inc" "${OCCT_ROOT}/include/opencascade"
  NO_DEFAULT_PATH REQUIRED)
find_library(OCCT_TKernel NAMES TKernel PATHS "${OCCT_ROOT}/win64/vc14/lib" "${OCCT_ROOT}/win64/vc18/lib" "${OCCT_ROOT}/win64/vc17/lib" "${OCCT_ROOT}/lib" NO_DEFAULT_PATH REQUIRED)
get_filename_component(OCCT_LIB_DIR "${OCCT_TKernel}" DIRECTORY)
get_filename_component(OCCT_BIN_DIR "${OCCT_LIB_DIR}/../bin" ABSOLUTE)

set(OCCT_TOOLKITS
  TKernel TKMath TKG2d TKG3d TKGeomBase TKBRep TKGeomAlgo TKTopAlgo TKPrim
  TKBO TKBool TKMesh TKShHealing)
set(OCCT_LIBS)
foreach(toolkit IN LISTS OCCT_TOOLKITS)
  find_library(OCCT_${toolkit} NAMES ${toolkit} PATHS "${OCCT_LIB_DIR}" NO_DEFAULT_PATH REQUIRED)
  list(APPEND OCCT_LIBS "${OCCT_${toolkit}}")
endforeach()

add_library(occt_bridge SHARED src/occt_bridge.cpp)
target_compile_features(occt_bridge PUBLIC cxx_std_17)
target_include_directories(occt_bridge PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include" PRIVATE "${OCCT_INCLUDE_DIR}")
target_link_libraries(occt_bridge PRIVATE ${OCCT_LIBS})
target_compile_definitions(occt_bridge PRIVATE OCCT_BRIDGE_BUILD)
set_target_properties(occt_bridge PROPERTIES CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN YES)

install(TARGETS occt_bridge RUNTIME DESTINATION "${OCCT_BIN_DIR}" LIBRARY DESTINATION "${OCCT_BIN_DIR}" ARCHIVE DESTINATION "${OCCT_LIB_DIR}")
install(FILES include/occt_bridge.h DESTINATION include)

# The runtime-loaded bridge must work on a desktop without a VC++ runtime
# installer. Copy only the MSVC redistributable DLLs this x64 build imports.
if(WIN32 AND MSVC)
  string(REGEX REPLACE "[/\\\\]Tools[/\\\\]MSVC[/\\\\][^/\\\\]+[/\\\\].*$" "" OCCT_BRIDGE_VC_ROOT "${CMAKE_CXX_COMPILER}")
  file(GLOB OCCT_BRIDGE_CRT_CANDIDATES LIST_DIRECTORIES TRUE
    "${OCCT_BRIDGE_VC_ROOT}/Redist/MSVC/*/x64/Microsoft.VC145.CRT")
  list(SORT OCCT_BRIDGE_CRT_CANDIDATES ORDER DESCENDING)
  if(OCCT_BRIDGE_CRT_CANDIDATES)
    list(GET OCCT_BRIDGE_CRT_CANDIDATES 0 OCCT_BRIDGE_CRT_DIR)
    foreach(crt msvcp140.dll vcruntime140.dll vcruntime140_1.dll)
      if(EXISTS "${OCCT_BRIDGE_CRT_DIR}/${crt}")
        install(FILES "${OCCT_BRIDGE_CRT_DIR}/${crt}" DESTINATION "${OCCT_BIN_DIR}")
      else()
        message(WARNING "Missing MSVC runtime DLL: ${OCCT_BRIDGE_CRT_DIR}/${crt}")
      endif()
    endforeach()
  else()
    message(WARNING "Could not locate the x64 MSVC 14.5 redistributable directory")
  endif()
endif()

option(OCCT_BRIDGE_BUILD_SMOKE "Build the local C ABI geometry smoke check" ON)
if(OCCT_BRIDGE_BUILD_SMOKE)
  enable_testing()
  add_executable(occt_bridge_smoke tests/smoke.cpp)
  target_link_libraries(occt_bridge_smoke PRIVATE occt_bridge)
  add_test(NAME occt_bridge_smoke COMMAND occt_bridge_smoke)
endif()
