/**
 * @file attack_mitigator.cc
 * @brief UAViSec mitigation controller.
 *
 * This executable does not perform an attack. It only publishes a mitigation
 * control command. The GZBridge receives the command and applies the mitigation
 * logic internally before publishing the sensor data to PX4.
 *
 * Usage:
 *   ./attack_mitigator --on      Enable all available mitigations
 *   ./attack_mitigator --off     Disable all mitigations
 *   ./attack_mitigator --status  Print usage information
 */

#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include <gz/transport.hh>
#include <gz/msgs.hh>

static constexpr const char *kMitigationTopic = "/gazebo/default/mitigation/control";

static gz::transport::Node node;

static void publishMitigationControl(bool enabled)
{
    auto pub = node.Advertise<gz::msgs::Vector3d>(kMitigationTopic);

    gz::msgs::Vector3d data;
    data.set_x(enabled ? 1.0 : 0.0); // X = mitigation enabled/disabled
    data.set_y(0.0);                 // Y = scope. 0 = all available mitigations
    data.set_z(0.0);                 // Z = reserved for future options

    // Give Gazebo Transport a brief moment to discover subscribers.
    for (int retry = 0; retry < 50 && !pub.HasConnections(); ++retry) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    // Publish several times to avoid losing the command during discovery.
    for (int i = 0; i < 10; ++i) {
        pub.Publish(data);
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    std::cout << "[AttackMitigator] Mitigation "
              << (enabled ? "ENABLED" : "DISABLED")
              << " | Scope=all" << std::endl;
}

static void printUsage()
{
    std::cout << "Usage:\n"
              << "  ./attack_mitigator --on      Enable all available mitigations\n"
              << "  ./attack_mitigator --off     Disable all mitigations\n"
              << "  ./attack_mitigator --status  Show this help message\n";
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        printUsage();
        return 0;
    }

    try {
        const std::string arg = argv[1];

        if (arg == "--on") {
            publishMitigationControl(true);
            return 0;
        }

        if (arg == "--off") {
            publishMitigationControl(false);
            return 0;
        }

        if (arg == "--status" || arg == "--help" || arg == "-h") {
            printUsage();
            return 0;
        }

        throw std::runtime_error("Unknown option: " + arg);

    } catch (const std::exception &e) {
        std::cerr << "Error: " << e.what() << std::endl;
        printUsage();
        return 1;
    }
}
