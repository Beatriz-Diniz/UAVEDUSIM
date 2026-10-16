/**
 * @file spoofer.cc
 * @brief Gazebo Sensor Attack Interface (CLI only).
 * This tool allows injecting offsets into various UAV sensors using Gazebo Transport.
 */

#include <string>
#include <iostream>
#include <vector>
#include <sstream>
#include <stdexcept>
#include <chrono>
#include <thread>
#include <algorithm>
#include <boost/program_options.hpp>

// Gazebo Harmonic transport and message headers
#include <gz/transport.hh>
#include <gz/msgs.hh>

namespace po = boost::program_options;

// Global Gazebo Transport Node
gz::transport::Node node;

/**
 * @brief Populates Gazebo Vector3d message.
 */
void fill_data(gz::msgs::Vector3d *data, std::vector<double> fill) {
    data->set_y(0.0);
    data->set_z(0.0);
    if (fill.size() > 0) data->set_x(fill[0]);
    if (fill.size() > 1) data->set_y(fill[1]);
    if (fill.size() > 2) data->set_z(fill[2]);
}

/**
 * @brief Prints the available commands and usage guide.
 */
void printUsage() {
    std::cout << "Usage: ./spoofer [options]" << std::endl;
    std::cout << "Available Commands:" << std::endl;
    std::cout << "  --jam <type:int> [intensity:double]  Jamming (0:Off, 1:Noise, 2:Blackout, 3:Pulsed)" << std::endl;
    std::cout << "  --abs <lat:double> <lon:double> <alt:double>" << std::endl;
    std::cout << "  --rot <deg:double> <rot:double>" << std::endl;
    std::cout << "  --imu <opt:int> <idx:int> <off:double>" << std::endl;
    std::cout << "  --stream <option:int>" << std::endl;
    std::cout << "  --motor <opt:int> <idx:int> <val:int>" << std::endl;
    std::cout << "  --mag <option:int>" << std::endl;
    std::cout << "  --lidar <opt:int> <off:double>" << std::endl;
    std::cout << "  --sonar <opt:int> <off:double>" << std::endl;
    std::cout << "  --baro <opt:int> <off:double>" << std::endl;
    std::cout << "Example: ./spoofer --jam 1 0.8" << std::endl;
}

/**
 * @brief Publishes sensor offset attacks.
 */
void publishSensorAttack(const std::string& topic, const std::vector<double>& values) {
    auto pub = node.Advertise<gz::msgs::Vector3d>(topic);

    gz::msgs::Vector3d data;
    fill_data(&data, values);

    // Espera até que uma conexão ativa seja estabelecida
    for (int retry = 0; retry < 50 && !pub.HasConnections(); ++retry) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    // Publica várias vezes para garantir a entrega
    for (int i = 0; i < 10; ++i) {
        pub.Publish(data);
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    // --- Impressão Corrigida ---
    // Agora imprime Y e Z apenas com base no tamanho do vetor (quantidade de argumentos passados)
    std::cout << "[Spoofer] Attack sent to topic: " << topic << " | X=" << values[0];
    
    if (values.size() > 1) {
        std::cout << " Y=" << values[1];
    }
    if (values.size() > 2) {
        std::cout << " Z=" << values[2];
    }
    std::cout << std::endl;
}

/**
 * @brief Publishes GPS Jamming commands.
 */
void publishJammingAttack(int type, float intensity) {
    auto pub = node.Advertise<gz::msgs::Vector3d>("/gazebo/default/attack/jamming");
    gz::msgs::Vector3d data;
    
    data.set_x(static_cast<double>(type)); // X = Attack Type
    data.set_y(intensity);                 // Y = Intensity
    data.set_z(0.0);
    
    for (int retry = 0; retry < 50 && !pub.HasConnections(); ++retry) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    for (int i = 0; i < 10; ++i) {
        pub.Publish(data);
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    
    // Log intensity only if the attack type uses it (Type 1, 3, etc.)
    std::cout << "[Spoofer] GPS Jamming: Type " << type;
    if (type != 0 && type != 2) {
        std::cout << " | Intensity: " << intensity;
    }
    std::cout << std::endl;
}

/**
 * @brief Publishes wind simulation parameters.
 */
void publishWindAttack(const std::vector<double>& values) {
    auto wind_pub = node.Advertise<gz::msgs::Vector3d>("/gazebo/default/attack/wind");
    auto wind_enable_pub = node.Advertise<gz::msgs::Int32>("/gazebo/default/attack/wind/enabled");

    gz::msgs::Vector3d data;
    fill_data(&data, values);

    gz::msgs::Int32 enable_data;
    enable_data.set_data(0); 

    for (int retry = 0; retry < 50 && (!wind_pub.HasConnections() || !wind_enable_pub.HasConnections()); ++retry) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    wind_pub.Publish(data);
    wind_enable_pub.Publish(enable_data);

    std::cout << "[Spoofer] Wind Attack | Direction: X=" << values[0] 
              << " Y=" << values[1] << " Z=" << values[2] << std::endl;
}

/**
 * @brief Publishes integer control commands (camera/stream).
 */
void publishInt(const std::string& topic, int value) {
    auto pub = node.Advertise<gz::msgs::Int32>(topic);
    gz::msgs::Int32 data;
    data.set_data(value);
    
    for (int retry = 0; retry < 50 && !pub.HasConnections(); ++retry) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    pub.Publish(data);
    
    std::cout << "[Spoofer] Integer command sent to " << topic 
              << " | Value: " << value << std::endl;
}

/**
 * @brief Parses CLI arguments.
 */
void processCommandLine(int _argc, char **_argv) {
    std::vector<double> abs_offsets; bool has_abs = false;
    std::vector<double> imu_offsets; bool has_imu = false;
    
    bool has_jam = false;
    int jam_type_cli = 0;
    double jam_intensity_cli = 0.0;

    std::vector<std::string> filtered_args;
    filtered_args.reserve(_argc);
    if (_argc > 0) filtered_args.push_back(_argv[0]);

    // Parsing loop (keeps arguments filtered)
    for (int i = 1; i < _argc; ++i) {
        std::string arg = _argv[i];

        if (arg == "--abs") {
            if (i + 3 >= _argc) throw std::runtime_error("Expected 3 values after --abs: lat lon alt");
            abs_offsets = { std::stod(_argv[i + 1]), std::stod(_argv[i + 2]), std::stod(_argv[i + 3]) };
            has_abs = true; i += 3; continue;
        }

        if (arg == "--imu") {
            if (i + 3 >= _argc) throw std::runtime_error("Expected 3 values after --imu: opt idx off");
            imu_offsets = { std::stod(_argv[i + 1]), std::stod(_argv[i + 2]), std::stod(_argv[i + 3]) };
            has_imu = true; i += 3; continue;
        }
        
        if (arg == "--jam") {
            has_jam = true;
            if (i + 1 < _argc && _argv[i+1][0] != '-') {
                try {
                    jam_type_cli = std::stoi(_argv[i + 1]);
                    i++;
                    if (i + 1 < _argc && _argv[i+1][0] != '-') {
                        jam_intensity_cli = std::stod(_argv[i + 1]);
                        i++;
                    } else {
                        jam_intensity_cli = 0.8; 
                    }
                } catch(...) { jam_type_cli = 1; jam_intensity_cli = 0.8; }
            }
            continue;
        }
        filtered_args.push_back(arg);
    }

    std::vector<char*> filtered_argv;
    filtered_argv.reserve(filtered_args.size());
    for (auto &arg : filtered_args) filtered_argv.push_back(arg.data());

    // Define all options as std::vector<double> to match publishSensorAttack signature
    po::options_description desc("Allowed options");
    desc.add_options()
        ("help", "Display help")
        ("jam", po::value<std::vector<double>>()->multitoken(), "Jamming: <type> [intensity]")
        ("abs", po::value<std::vector<double>>()->multitoken(), "Absolute: <lat> <lon> <alt>")
        ("rot", po::value<std::vector<double>>()->multitoken(), "Rotation: <deg> <rot>")
        ("imu", po::value<std::vector<double>>()->multitoken(), "IMU: <opt> <idx> <off>")
        ("stream", po::value<std::vector<double>>()->multitoken(), "Stream: <option>")
        ("motor", po::value<std::vector<double>>()->multitoken(), "Motor: <opt> <idx> <val>")
        ("mag", po::value<std::vector<double>>()->multitoken(), "Mag: <option>")
        ("lidar", po::value<std::vector<double>>()->multitoken(), "Lidar: <opt> <dist>")
        ("sonar", po::value<std::vector<double>>()->multitoken(), "Sonar: <opt> <dist>")
        ("baro", po::value<std::vector<double>>()->multitoken(), "Baro: <opt> <off>");

    po::variables_map vm;
    // Desabilita opcoes curtas de letra unica (o spoofer so usa "--flag"
    // longas): sem isso, o boost tenta interpretar "-2.5" como a opcao
    // curta "-2" seguida de ".5", em vez de um numero negativo valido.
    po::store(po::command_line_parser(static_cast<int>(filtered_argv.size()), filtered_argv.data())
        .options(desc)
        .style(po::command_line_style::unix_style ^ po::command_line_style::allow_short)
        .run(), vm);
    po::notify(vm);

    // Apply logic
    if (has_abs) publishSensorAttack("/gazebo/default/attack/gps", abs_offsets);
    if (has_imu) publishSensorAttack("/gazebo/default/attack/imu", imu_offsets);
    if (has_jam) publishJammingAttack(jam_type_cli, static_cast<float>(jam_intensity_cli));

    if (vm.count("rot")) publishSensorAttack("/gazebo/default/attack/gps_rot", vm["rot"].as<std::vector<double>>());
    if (vm.count("lidar")) publishSensorAttack("/gazebo/default/attack/lidar", vm["lidar"].as<std::vector<double>>());
    if (vm.count("sonar")) publishSensorAttack("/gazebo/default/attack/sonar", vm["sonar"].as<std::vector<double>>());
    if (vm.count("baro")) publishSensorAttack("/gazebo/default/attack/baro", vm["baro"].as<std::vector<double>>());
    if (vm.count("mag")) publishSensorAttack("/gazebo/default/attack/mag", vm["mag"].as<std::vector<double>>());
    if (vm.count("stream")) publishInt("/gazebo/default/attack/stream", static_cast<int>(vm["stream"].as<std::vector<double>>()[0]));
    if (vm.count("motor")) publishSensorAttack("/gazebo/default/attack/motor", vm["motor"].as<std::vector<double>>());   
    
    if (vm.count("help")) {
        printUsage();
        return;
    } 
}

int main(int _argc, char **_argv) {
    // If arguments provided, process them
    if (_argc > 1) {
        try {
            processCommandLine(_argc, _argv);
        } catch (const std::exception& e) {
            std::cerr << "Error: " << e.what() << std::endl;
            printUsage();
            return 1;
        }
    } else {
        // Otherwise, show help menu
        printUsage();
    }
    return 0;
}