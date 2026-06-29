cmake_minimum_required(VERSION 3.10)
project(uavisec_spoofer)

# Guia o CMake até a instalação local do Gazebo, se necessário.
list(APPEND CMAKE_PREFIX_PATH "$ENV{HOME}/gazebo_workspace/install")

# Bibliotecas de transporte e mensagens do Gazebo Harmonic.
find_package(gz-transport13 REQUIRED)
find_package(gz-msgs10 REQUIRED)
find_package(Boost REQUIRED COMPONENTS program_options)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include_directories(
    ${Boost_INCLUDE_DIRS}
)

add_executable(spoofer
    spoofer.cc
)

add_executable(attack_mitigator
    attack_mitigator.cc
)

target_link_libraries(spoofer
    gz-transport13::core
    gz-msgs10::core
    ${Boost_LIBRARIES}
    pthread
)

target_link_libraries(attack_mitigator
    gz-transport13::core
    gz-msgs10::core
    pthread
)
