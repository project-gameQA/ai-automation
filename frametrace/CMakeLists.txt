cmake_minimum_required(VERSION 3.16)
project(frametrace VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

option(FRAMETRACE_BUILD_TESTS "테스트와 벤치마크를 빌드한다" ON)
option(FRAMETRACE_BUILD_DEMO "데모 워크로드를 빌드한다" ON)
option(FRAMETRACE_BUILD_TOOLS "ft_dump CLI를 빌드한다" ON)

# ---------------------------------------------------------------------------
# 라이브러리
# ---------------------------------------------------------------------------

add_library(frametrace STATIC
    src/frametrace.cpp
    src/trace_reader.cpp
)

target_include_directories(frametrace PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

find_package(Threads REQUIRED)
target_link_libraries(frametrace PUBLIC Threads::Threads)

if(MSVC)
    target_compile_options(frametrace PRIVATE /W4)
else()
    target_compile_options(frametrace PRIVATE -Wall -Wextra)
endif()

add_library(frametrace::frametrace ALIAS frametrace)

# 계측을 완전히 제거한 변종. 오버헤드 0을 실제로 증명하기 위해 존재한다.
# 주장만 하는 것과 별도 바이너리로 보여주는 건 다르다.
add_library(frametrace_disabled STATIC
    src/frametrace.cpp
    src/trace_reader.cpp
)
target_include_directories(frametrace_disabled PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_compile_definitions(frametrace_disabled PUBLIC FT_ENABLED=0)
target_link_libraries(frametrace_disabled PUBLIC Threads::Threads)

# ---------------------------------------------------------------------------
# 도구
# ---------------------------------------------------------------------------

if(FRAMETRACE_BUILD_TOOLS)
    add_executable(ft_dump tools/ft_dump/main.cpp)
    target_link_libraries(ft_dump PRIVATE frametrace)
endif()

# ---------------------------------------------------------------------------
# 데모
# ---------------------------------------------------------------------------

if(FRAMETRACE_BUILD_DEMO)
    add_executable(ft_demo demo/demo_workload.cpp)
    target_link_libraries(ft_demo PRIVATE frametrace)
endif()

# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------

if(FRAMETRACE_BUILD_TESTS)
    enable_testing()

    add_executable(test_ring_buffer tests/test_ring_buffer.cpp)
    target_link_libraries(test_ring_buffer PRIVATE frametrace)
    add_test(NAME ring_buffer COMMAND test_ring_buffer)

    add_executable(bench_overhead tests/bench_overhead.cpp)
    target_link_libraries(bench_overhead PRIVATE frametrace)

    add_executable(bench_overhead_disabled tests/bench_overhead.cpp)
    target_link_libraries(bench_overhead_disabled PRIVATE frametrace_disabled)
endif()
